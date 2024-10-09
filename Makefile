# Define the name of the Docker image
IMAGE_NAME = my-trading-bot

# Dockerfile commands
build:
	docker build -t $(IMAGE_NAME) .

run:
	docker run --env-file .env $(IMAGE_NAME)

# Clean up Docker resources
clean:
	docker system prune -f

# Rebuild the image from scratch
rebuild: clean build

# Run in detached mode
run-detached:
	docker run -d --env-file .env $(IMAGE_NAME)
