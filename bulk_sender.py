#!/usr/bin/env python3
"""
Bulk Email Sender using Resend API
===================================
Sends personalized emails with embedded images and PDF attachments
to a list of recipients from a CSV file.

Usage:
    python bulk_sender.py              # Interactive mode with confirmation
    python bulk_sender.py --yes        # Auto-confirm (skip prompt)
    python bulk_sender.py --dry-run    # Preview only, don't send
"""

import os
import sys
import csv
import base64
import time
import re
from pathlib import Path
from datetime import datetime

import resend
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Confirm
from rich import print as rprint

# Initialize Rich console
console = Console()

# Load environment variables
load_dotenv()

# Parse command line arguments
AUTO_CONFIRM = '--yes' in sys.argv or '-y' in sys.argv
DRY_RUN = '--dry-run' in sys.argv


def validate_email(email: str) -> bool:
    """Validate email address format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def load_csv(file_path: str) -> list[dict]:
    """Load and validate CSV file with email addresses."""
    recipients = []
    invalid_emails = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        # Check required columns
        if 'email' not in reader.fieldnames:
            raise ValueError("CSV must have 'email' column")
        
        for row_num, row in enumerate(reader, start=2):
            email = row.get('email', '').strip()
            name = row.get('name', '').strip() or 'Valued Customer'
            
            if not email:
                continue
                
            if validate_email(email):
                recipients.append({
                    'email': email,
                    'name': name
                })
            else:
                invalid_emails.append((row_num, email))
    
    if invalid_emails:
        console.print(f"\n[yellow]Warning: {len(invalid_emails)} invalid email(s) found:[/yellow]")
        for row_num, email in invalid_emails[:5]:
            console.print(f"  Row {row_num}: {email}")
        if len(invalid_emails) > 5:
            console.print(f"  ... and {len(invalid_emails) - 5} more")
    
    return recipients


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
    return html.replace('{{name}}', name)


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


def show_preview(recipients: list[dict], html_template: str, config: dict):
    """Show a preview of the email before sending."""
    console.print("\n")
    console.print(Panel.fit(
        "[bold cyan]📧 BULK EMAIL SENDER - PREVIEW[/bold cyan]",
        border_style="cyan"
    ))
    
    # Email configuration table
    config_table = Table(title="Email Configuration", show_header=False, border_style="blue")
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value", style="white")
    
    config_table.add_row("From", f"{config['from_name']} <{config['from_email']}>")
    config_table.add_row("Subject", config['subject'])
    config_table.add_row("Image File", config['image_file'])
    config_table.add_row("PDF Attachment", config['pdf_file'])
    config_table.add_row("Rate Limit", f"{config['rate_limit']} emails/second")
    
    console.print(config_table)
    
    # Recipients summary
    console.print(f"\n[bold]Total Recipients:[/bold] [green]{len(recipients)}[/green]")
    
    # Show first 5 recipients
    if recipients:
        recipients_table = Table(title="Sample Recipients (first 5)", border_style="green")
        recipients_table.add_column("#", style="dim")
        recipients_table.add_column("Email", style="cyan")
        recipients_table.add_column("Name", style="white")
        
        for i, recipient in enumerate(recipients[:5], 1):
            recipients_table.add_row(str(i), recipient['email'], recipient['name'])
        
        if len(recipients) > 5:
            recipients_table.add_row("...", f"... and {len(recipients) - 5} more", "")
        
        console.print(recipients_table)
    
    # Show sample personalized content
    console.print("\n[bold]Sample Email Preview:[/bold]")
    sample_name = recipients[0]['name'] if recipients else "John Doe"
    sample_greeting = f"Dear {sample_name},"
    console.print(Panel(
        f"[italic]{sample_greeting}[/italic]\n\n"
        "[dim](Newsletter image will be embedded here)[/dim]\n\n"
        "We hope this email finds you well. Please find attached our latest "
        "newsletter with important updates and information.\n\n"
        "[dim]PDF attachment: newsletter.pdf[/dim]",
        title="Email Body Preview",
        border_style="yellow"
    ))
    
    # Estimated time
    estimated_time = len(recipients) / config['rate_limit']
    console.print(f"\n[bold]Estimated Time:[/bold] ~{estimated_time:.1f} seconds ({estimated_time/60:.1f} minutes)")


def send_emails(recipients: list[dict], html_template: str, config: dict) -> tuple[int, int, list]:
    """Send emails to all recipients with progress tracking."""
    
    # Configure Resend
    resend.api_key = config['api_key']
    
    # Load files
    image_base64 = load_file_as_base64(config['image_file'])
    pdf_base64 = load_file_as_base64(config['pdf_file'])
    
    image_mime = get_mime_type(config['image_file'])
    pdf_filename = Path(config['pdf_file']).name
    
    sent_count = 0
    failed_count = 0
    failed_emails = []
    
    delay = 1.0 / config['rate_limit']
    
    console.print("\n[bold cyan]🚀 Starting email delivery...[/bold cyan]\n")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[cyan]{task.completed}/{task.total}[/cyan]"),
        console=console
    ) as progress:
        task = progress.add_task("Sending emails", total=len(recipients))
        
        for recipient in recipients:
            try:
                # Personalize HTML
                personalized_html = personalize_html(html_template, recipient['name'])
                
                # Prepare email params
                params = {
                    "from": f"{config['from_name']} <{config['from_email']}>",
                    "to": [recipient['email']],
                    "subject": config['subject'],
                    "html": personalized_html,
                    "attachments": [
                        {
                            "filename": "newsletter_image.png",
                            "content": image_base64,
                            "content_type": image_mime,
                            "content_id": "newsletter_image"
                        },
                        {
                            "filename": pdf_filename,
                            "content": pdf_base64,
                            "content_type": "application/pdf"
                        }
                    ]
                }
                
                # Send email
                resend.Emails.send(params)
                sent_count += 1
                
            except Exception as e:
                failed_count += 1
                failed_emails.append({
                    'email': recipient['email'],
                    'name': recipient['name'],
                    'error': str(e)
                })
            
            progress.update(task, advance=1)
            
            # Rate limiting
            time.sleep(delay)
    
    return sent_count, failed_count, failed_emails


def save_failed_log(failed_emails: list, log_file: str):
    """Save failed emails to a log file for retry."""
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("email,name,error,timestamp\n")
        timestamp = datetime.now().isoformat()
        for entry in failed_emails:
            error = entry['error'].replace('"', '""')
            f.write(f"{entry['email']},{entry['name']},\"{error}\",{timestamp}\n")


def main():
    """Main function to run the bulk email sender."""
    console.print(Panel.fit(
        "[bold magenta]╔══════════════════════════════════════╗\n"
        "║     BULK EMAIL SENDER v1.0           ║\n"
        "║     Powered by Resend API            ║\n"
        "╚══════════════════════════════════════╝[/bold magenta]",
        border_style="magenta"
    ))
    
    # Load configuration
    config = {
        'api_key': os.getenv('RESEND_API_KEY'),
        'from_email': os.getenv('FROM_EMAIL'),
        'from_name': os.getenv('FROM_NAME', 'Support'),
        'subject': os.getenv('EMAIL_SUBJECT', 'Newsletter'),
        'csv_file': os.getenv('CSV_FILE', 'emails.csv'),
        'image_file': os.getenv('IMAGE_FILE', 'newsletter.png'),
        'pdf_file': os.getenv('PDF_FILE', 'newsletter.pdf'),
        'html_template': os.getenv('HTML_TEMPLATE', 'template.html'),
        'rate_limit': int(os.getenv('RATE_LIMIT', '2'))
    }
    
    # Validate required configuration
    missing = []
    if not config['api_key']:
        missing.append('RESEND_API_KEY')
    if not config['from_email']:
        missing.append('FROM_EMAIL')
    
    if missing:
        console.print(f"\n[red]❌ Error: Missing required environment variables:[/red]")
        for var in missing:
            console.print(f"   - {var}")
        console.print("\n[yellow]Please copy .env.example to .env and fill in the values.[/yellow]")
        return
    
    # Check required files exist
    required_files = [
        ('CSV file', config['csv_file']),
        ('Image file', config['image_file']),
        ('PDF file', config['pdf_file']),
        ('HTML template', config['html_template'])
    ]
    
    missing_files = []
    for name, path in required_files:
        if not Path(path).exists():
            missing_files.append((name, path))
    
    if missing_files:
        console.print(f"\n[red]❌ Error: Required files not found:[/red]")
        for name, path in missing_files:
            console.print(f"   - {name}: {path}")
        return
    
    # Load data
    console.print("\n[cyan]Loading data...[/cyan]")
    
    try:
        recipients = load_csv(config['csv_file'])
        html_template = load_html_template(config['html_template'])
    except Exception as e:
        console.print(f"\n[red]❌ Error loading data: {e}[/red]")
        return
    
    if not recipients:
        console.print("\n[red]❌ Error: No valid recipients found in CSV file.[/red]")
        return
    
    # Show preview
    show_preview(recipients, html_template, config)
    
    # Handle dry-run mode
    if DRY_RUN:
        console.print("\n[cyan]🔍 DRY RUN MODE - No emails will be sent.[/cyan]")
        console.print("[green]✅ Preview complete. Remove --dry-run flag to send emails.[/green]")
        return
    
    # Confirm before sending
    console.print("\n")
    if AUTO_CONFIRM:
        console.print("[yellow]Auto-confirm enabled (--yes flag)[/yellow]")
    elif not Confirm.ask("[bold yellow]Do you want to proceed with sending emails?[/bold yellow]"):
        console.print("\n[yellow]Email sending cancelled.[/yellow]")
        return
    
    # Send emails
    sent, failed, failed_emails = send_emails(recipients, html_template, config)
    
    # Show summary
    console.print("\n")
    console.print(Panel.fit(
        f"[bold green]✅ EMAIL DELIVERY COMPLETE[/bold green]\n\n"
        f"[green]Sent successfully:[/green] {sent}\n"
        f"[red]Failed:[/red] {failed}",
        title="Summary",
        border_style="green"
    ))
    
    # Save failed log if any
    if failed_emails:
        log_file = f"failed_emails_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        save_failed_log(failed_emails, log_file)
        console.print(f"\n[yellow]Failed emails logged to: {log_file}[/yellow]")
        console.print("[dim]You can retry these emails by using this file as input.[/dim]")


if __name__ == "__main__":
    main()

