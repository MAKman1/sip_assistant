# Use an official Python runtime as a parent image
# Using Python 3.11 to ensure compatibility with features like ExceptionGroup
FROM python:3.11-slim

# Set environment variables to prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that might be needed (if any - audioop is built-in)
# RUN apt-get update && apt-get install -y --no-install-recommends some-package && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size
# --trusted-host pypi.python.org helps in some network environments
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 8081 available to the world outside this container
EXPOSE 8081

# Define environment variables needed by the application
# These should be provided at runtime, e.g., using docker run -e or --env-file
# ENV GOOGLE_API_KEY=your_google_api_key
# ENV TWILIO_ACCOUNT_SID=your_twilio_account_sid
# ENV TWILIO_AUTH_TOKEN=your_twilio_auth_token
# ENV TWILIO_PHONE_NUMBER=your_twilio_phone_number
# ENV PUBLIC_APP_HOST=your_public_app_host

# Run app.py when the container launches
CMD ["python", "app.py"]
