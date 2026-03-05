FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY bulk_sender.py .
COPY template.html .

# The following files should be mounted at runtime:
# - .env (configuration)
# - emails.csv (recipient list)
# - newsletter.png (image for email body)
# - newsletter.pdf (attachment)

CMD ["python", "bulk_sender.py"]


