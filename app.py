#!/usr/bin/env python3
"""
Bulk Email Sender - Web UI
==========================
Flask-based web interface for sending bulk emails via Resend API.
"""

import os
import csv
import base64
import re
import time
from pathlib import Path
from datetime import datetime
from threading import Thread

import resend
from flask import Flask, render_template, request, jsonify, redirect, url_for
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload
app.config['UPLOAD_FOLDER'] = 'uploads'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Global state for tracking send progress
send_state = {
    'is_sending': False,
    'total': 0,
    'sent': 0,
    'failed': 0,
    'errors': [],
    'complete': False
}


def validate_email(email: str) -> bool:
    """Validate email address format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def load_csv(file_path: str) -> tuple[list[dict], list[dict]]:
    """Load and validate CSV file with email addresses."""
    recipients = []
    invalid_emails = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        if 'email' not in reader.fieldnames:
            raise ValueError("CSV must have 'email' column")
        
        for row_num, row in enumerate(reader, start=2):
            email = row.get('email', '').strip()
            name = row.get('name', '').strip()
            
            if not email:
                continue
                
            if validate_email(email):
                recipients.append({
                    'email': email,
                    'name': name
                })
            else:
                invalid_emails.append({'row': row_num, 'email': email})
    
    return recipients, invalid_emails


def load_file_as_base64(file_path: str) -> str:
    """Load a file and return its base64 encoded content."""
    with open(file_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def load_html_template(template_path: str) -> str:
    """Load HTML template from file."""
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


def personalize_html(html: str, name: str) -> str:
    """Replace placeholders in HTML with actual values."""
    if name:
        greeting = f"Hi {name},"
    else:
        greeting = "Hi,"
    html = html.replace('{{greeting}}', greeting)
    html = html.replace('{{name}}', name)
    return html


def get_mime_type(file_path: str) -> str:
    """Get MIME type based on file extension."""
    ext = Path(file_path).suffix.lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.pdf': 'application/pdf',
    }
    return mime_types.get(ext, 'application/octet-stream')


def get_config():
    """Get configuration from environment variables."""
    return {
        'api_key': os.getenv('RESEND_API_KEY'),
        'from_email': os.getenv('FROM_EMAIL'),
        'from_name': os.getenv('FROM_NAME', 'Support'),
        'subject': os.getenv('EMAIL_SUBJECT', 'Newsletter'),
        'csv_file': os.getenv('CSV_FILE', 'emails.csv'),
        'image_file': os.getenv('IMAGE_FILE', 'newsletter.png'),
        'pdf_file': os.getenv('PDF_FILE', 'newsletter.pdf'),
        'pdf_original_name': os.getenv('PDF_ORIGINAL_NAME', 'SAIF - Newsletter Nov2025.pdf'),
        'html_template': os.getenv('HTML_TEMPLATE', 'template.html'),
        'rate_limit': int(os.getenv('RATE_LIMIT', '2'))
    }


def send_emails_async(recipients, html_template, config):
    """Send emails in background thread."""
    global send_state
    
    send_state = {
        'is_sending': True,
        'total': len(recipients),
        'sent': 0,
        'failed': 0,
        'errors': [],
        'complete': False
    }
    
    resend.api_key = config['api_key']

    delay = 1.0 / config['rate_limit']
    
    for recipient in recipients:
        try:
            personalized_html = personalize_html(html_template, recipient['name'])
            
            params = {
                "from": f"{config['from_name']} <{config['from_email']}>",
                "to": [recipient['email']],
                "subject": config['subject'],
                "html": personalized_html
            }
            
            resend.Emails.send(params)
            send_state['sent'] += 1
            
        except Exception as e:
            send_state['failed'] += 1
            send_state['errors'].append({
                'email': recipient['email'],
                'error': str(e)
            })
        
        time.sleep(delay)
    
    send_state['is_sending'] = False
    send_state['complete'] = True


@app.route('/')
def index():
    """Main page with preview and send controls."""
    config = get_config()
    
    # Check configuration
    errors = []
    if not config['api_key']:
        errors.append('RESEND_API_KEY not configured')
    if not config['from_email']:
        errors.append('FROM_EMAIL not configured')
    
    # Check files
    files_status = {
        'csv': Path(config['csv_file']).exists(),
        'image': Path(config['image_file']).exists(),
        'pdf': Path(config['pdf_file']).exists(),
        'template': Path(config['html_template']).exists()
    }
    
    # Load recipients if CSV exists
    recipients = []
    invalid_emails = []
    if files_status['csv']:
        try:
            recipients, invalid_emails = load_csv(config['csv_file'])
        except Exception as e:
            errors.append(f'Error loading CSV: {str(e)}')
    
    # Load HTML template for preview
    email_preview_html = ""
    if files_status['template']:
        try:
            email_preview_html = load_html_template(config['html_template'])
            # Replace placeholder with sample name
            sample_name = recipients[0]['name'] if recipients else "John Doe"
            email_preview_html = personalize_html(email_preview_html, sample_name)
        except Exception as e:
            errors.append(f'Error loading template: {str(e)}')
    
    return render_template('index.html',
        config=config,
        errors=errors,
        files_status=files_status,
        recipients=recipients,
        total_recipients=len(recipients),
        invalid_emails=invalid_emails,
        send_state=send_state,
        email_preview_html=email_preview_html
    )


@app.route('/upload-csv', methods=['POST'])
def upload_csv():
    """Handle CSV file upload."""
    if 'csv_file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['csv_file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'File must be a CSV'}), 400
    
    # Save to the configured CSV path
    config = get_config()
    file.save(config['csv_file'])
    
    # Validate the uploaded CSV
    try:
        recipients, invalid_emails = load_csv(config['csv_file'])
        return jsonify({
            'success': True,
            'total': len(recipients),
            'invalid': len(invalid_emails)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/preview')
def preview():
    """Get email preview HTML."""
    config = get_config()
    from_name = request.args.get('from_name') or config['from_name']
    subject = request.args.get('subject') or config['subject']

    try:
        html_template = load_html_template(config['html_template'])
        recipients, _ = load_csv(config['csv_file'])
        sample_name = recipients[0]['name'] if recipients else "John Doe"
        preview_html = personalize_html(html_template, sample_name)

        return jsonify({
            'html': preview_html,
            'subject': subject,
            'from': f"{from_name} <{config['from_email']}>",
            'pdf_name': config.get('pdf_original_name') or ''
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/send', methods=['POST'])
def send():
    """Start sending emails."""
    global send_state

    if send_state['is_sending']:
        return jsonify({'error': 'Already sending emails'}), 400

    config = get_config()

    # Override from_name and subject from request body
    data = request.get_json(silent=True) or {}
    if data.get('from_name'):
        config['from_name'] = data['from_name']
    if data.get('subject'):
        config['subject'] = data['subject']

    try:
        recipients, _ = load_csv(config['csv_file'])
        html_template = load_html_template(config['html_template'])
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    # Start sending in background
    thread = Thread(target=send_emails_async, args=(recipients, html_template, config))
    thread.start()

    return jsonify({'status': 'started', 'total': len(recipients)})


@app.route('/status')
def status():
    """Get current send status."""
    return jsonify(send_state)


@app.route('/reset', methods=['POST'])
def reset():
    """Reset send state."""
    global send_state
    send_state = {
        'is_sending': False,
        'total': 0,
        'sent': 0,
        'failed': 0,
        'errors': [],
        'complete': False
    }
    return jsonify({'status': 'reset'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
