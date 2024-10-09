# Use an official Python runtime as a base image
FROM python:3.9-slim

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -

# Add Poetry to PATH
ENV PATH="/root/.local/bin:$PATH"

# Set the working directory in the container
WORKDIR /app

# Copy the pyproject.toml and poetry.lock files to the working directory
COPY pyproject.toml poetry.lock* ./

# Install dependencies using Poetry
RUN poetry install --no-root

# Copy the rest of the application files to the working directory
COPY . .

# Run the Python script when the container launches
CMD ["poetry", "run", "python", "main.py"]
