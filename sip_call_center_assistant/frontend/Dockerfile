# Use an official Node runtime as a parent image
FROM node:lts-alpine

# Set the working directory
WORKDIR /app

# Copy package.json and package-lock.json
COPY package*.json ./

# Install dependencies using npm ci for consistency
RUN npm ci

# Copy the rest of the application source code
COPY . .

# Expose the Vite development server port (default is 5173)
EXPOSE 5173

# Run the development server
# The '--host' flag makes Vite listen on all available network interfaces (0.0.0.0),
# which is necessary for accessing it from outside the Docker container.
CMD ["npm", "run", "dev", "--", "--host"]
