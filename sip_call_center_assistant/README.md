# Call Assistant Backend

This document outlines the steps required to set up and run the Call Assistant backend application.

## Prerequisites

- Python 3.x installed
- `pip` (Python package installer)

## Setup

1.  **Set up Virtual Environment:**
    Navigate to the `call_assistant_backend` directory in your terminal and run the setup script:
    ```bash
    ./setup_venv.sh
    ```
    This will create a virtual environment named `venv` and activate it.

2.  **Install Dependencies:**
    While the virtual environment is active, install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment Variables:**
    Create a `.env` file in the `call_assistant_backend` directory. This file should contain necessary environment variables for the application to run (e.g., API keys, configuration settings). Refer to the application code or documentation for the specific variables required.

    Example `.env` structure:
    ```
    API_KEY=your_api_key_here
    OTHER_VARIABLE=value
    ```

## Running the Application

1.  **Activate Virtual Environment (if not already active):**
    ```bash
    source venv/bin/activate
    ```

2.  **Run the Application:**
    Execute the run script:
    ```bash
    ./run.sh
    ```
    This script will typically start the Flask development server (or whichever framework is used in `app.py`).

## Stopping the Application

- Press `Ctrl+C` in the terminal where the application is running.
- Deactivate the virtual environment when done:
  ```bash
  deactivate
