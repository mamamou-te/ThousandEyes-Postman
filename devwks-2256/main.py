
import aiohttp
import asyncio
import getpass
import time
from datetime import datetime, timezone
from itertools import count
import requests


# ANSI escape codes for colors
GREEN = '\033[92m'
RED = '\033[91m'
RESET = '\033[0m'

# Declare the queue as a global variable
queue = asyncio.Queue()
new_requests = 10
remaining_limit = 2 * new_requests
async def fetch(session, url, headers, initial_index, request_index, counts):
    """
    Fetch data from the API endpoint and handle pagination.

    Parameters:
    - session: The aiohttp session object.
    - url: The API endpoint URL to fetch data from.
    - headers: The HTTP headers to use for the request.
    - initial_index: The index of the initial URL in the list.
    - request_index: The index of the request in the overall sequence.
    - counts: Dictionary to track successful and failed requests.
    """
    # print(f"request index {request_index}, initial index {initial_index}")
    global queue  # Reference the global queue
    async with session.get(url, headers=headers) as response:
        status = response.status
        response_data = await response.json()

        if 200 <= status < 300:
            counts['success'] += 1
            # print(f"{GREEN}Request {request_index + 1} (Initial Index {initial_index + 1}): Status Code {status}{RESET}")
        else:
            counts['failure'] += 1
            error_reason = await response.text()
            print(f"{RED}Request {request_index + 1} (Initial Index {initial_index + 1}): Status Code {status}, Error: {error_reason}{RESET}")

        # Check for the next page URL in the response
        next_page = response_data.get('_links', {}).get('next', {}).get('href')
        if next_page:
            next_request_index = next(index_counter)
            await queue.put((next_page, initial_index, next_request_index))
        return response.headers

async def worker(session, headers, counts, all_tasks):
    """
    Worker function that processes a batch of requests from the queue.

    Parameters:
    - session: The aiohttp session object.
    - headers: The HTTP headers to use for requests.
    - counts: Dictionary to track successful and failed requests.
    - all_tasks: List to store all tasks for final processing.
    """
    global queue  # Reference the global queue
    global index_counter
    global remaining_limit
    global new_requests
    batch_index = 0  # Initialize batch index

    while not queue.empty():
        # Collect a batch of up to 10 requests
        batch = [await queue.get() for _ in range(min(new_requests, queue.qsize()))]
        batch_index += 1  # Increment batch index for each new batch

        print(f"Processing Batch {batch_index} with {new_requests} requests")

        # Record the start time of the batch processing
        batch_start_time = datetime.now()

        # Capture counts before processing the batch
        success_before = counts['success']
        failure_before = counts['failure']

        # Create tasks for each fetch operation in the batch
        tasks = [
            fetch(session, url, headers, initial_index, request_index, counts)
            for url, initial_index, request_index in batch
        ]

        # Add tasks to the global list for later awaiting
        all_tasks.extend(tasks)

        # Wait for the first task to complete
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # Process the first completed task
        for completed_task in done:
            try:
                response_headers = await completed_task
                remaining = int(response_headers.get('X-Organization-Rate-Limit-Remaining', 0))
                reset = int(response_headers.get('X-Organization-Rate-Limit-Reset', time.time()))

                # Check if the rate limit is approaching
                if remaining <= remaining_limit:
                    reset_time = datetime.fromtimestamp(reset, tz=timezone.utc)
                    current_time = datetime.now(timezone.utc)
                    time_left = (reset_time - current_time).total_seconds()

                    if time_left > 0:
                        print(f"Batch {batch_index}: Rate limit approaching. Waiting until {reset_time.strftime('%Y-%m-%d %H:%M:%S')} UTC for reset.")
                        print(f"Batch {batch_index}: Time left until reset: {int(time_left)} seconds.")
                        time.sleep(time_left)

            except Exception as e:
                print(f"Error processing completed task: {e}")

        # Calculate and display the number of successful and failed requests for the batch
        success_after = counts['success']
        failure_after = counts['failure']
        print(f"Batch {batch_index} Results: {success_after - success_before} successful, {failure_after - failure_before} failed")

        # Record the end time of the batch processing
        batch_end_time = datetime.now()

        # Calculate and display the duration of the batch processing
        batch_duration = (batch_end_time - batch_start_time).total_seconds()
        print(f"Batch {batch_index} Duration: {batch_duration:.2f} seconds")

        # Mark the batch items as done
        for _ in batch:
            queue.task_done()

def get_test_list(bearer_token):
    url = "https://api.thousandeyes.com/v7/tests"
    payload = {}
    headers = {
    'Accept': 'application/hal+json',
    'Authorization': f'Bearer {bearer_token}'
    }

    response = requests.request("GET", url, headers=headers, data=payload)
    response_data = response.json()
    tests_list = response_data.get("tests", [])
    test_id_list = []
    for test in tests_list:
        test_name = test.get("testName")
        test_id = test.get("testId")
        test_id_list.append(test_id)
        print(f"Test name: '{test_name}' with test ID: '{test_id}'")
    selected_test_id = input("Select a test ID: ")
    if selected_test_id not in test_id_list:
        raise ValueError("Select an existing test ID")
    return selected_test_id
    



async def run_api_calls(secret_token):
    """
    Main function to initiate API calls using the provided secret token.

    Parameters:
    - secret_token: The authentication token for the API.
    """
    headers = {
        'Accept':  'application/hal+json',
        'Authorization': f'Bearer {secret_token}'
    }

    counts = {'success': 0, 'failure': 0}
    all_tasks = []  # List to store all tasks
    test_id = get_test_list(secret_token)
    # List of initial URLs
    initial_urls = [
        f"https://api.thousandeyes.com/v7/test-results/{test_id}/network?window=7d"
    ] * 125

    global index_counter
    index_counter = count()  # Initialize a global counter

    global queue  # Reference the global queue
    for initial_index, url in enumerate(initial_urls):
        request_index = next(index_counter)
        await queue.put((url, initial_index, request_index))

    # Record the start time of the entire process
    total_start_time = datetime.now()

    async with aiohttp.ClientSession() as session:
        # Start a single worker to process the queue in batches
        worker_task = asyncio.create_task(worker(session, headers, counts, all_tasks))
        await queue.join()  # Wait for all tasks in the queue to be processed
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Now await all tasks to ensure they have completed
        await asyncio.gather(*all_tasks, return_exceptions=True)

    # Record the end time of the entire process
    total_end_time = datetime.now()

    # Calculate and display the total duration of the entire process
    total_duration = (total_end_time - total_start_time).total_seconds()
    print(f"\nTotal Duration: {total_duration:.2f} seconds")

    print(f"Total Successful Requests: {counts['success']}")
    print(f"Total Failed Requests: {counts['failure']}")
    


if __name__ == "__main__":
    secret_token = getpass.getpass("Please enter your secret token: ")
    asyncio.run(run_api_calls(secret_token))
