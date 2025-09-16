#!/usr/bin/env python
"""
Script to migrate existing media files to Amazon S3.
This script should be run after S3 is configured in settings.py and the S3 bucket is created.

Usage:
    python migrate_to_s3.py

Requirements:
    - Django settings should be configured with AWS_STORAGE_BUCKET_NAME and other AWS settings
    - boto3 and django-storages should be installed
"""

import os
import sys
import django
from django.conf import settings
from django.core.files.storage import default_storage
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "connect.settings")
django.setup()

# Ensure that S3 is configured
if not getattr(settings, 'USE_S3', False):
    print("S3 is not configured in settings. Please set USE_S3=TRUE in .env file.")
    sys.exit(1)

def upload_file_to_s3(file_path, s3_path):
    """Upload a file to S3"""
    try:
        with open(file_path, 'rb') as file_data:
            default_storage.save(s3_path, file_data)
        print(f"Uploaded {file_path} to {s3_path}")
        return True
    except Exception as e:
        print(f"Error uploading {file_path}: {e}")
        return False

def migrate_media_to_s3():
    """Migrate all media files to S3"""
    media_root = settings.MEDIA_ROOT
    media_dir = Path(media_root)
    
    if not media_dir.exists():
        print(f"Media directory {media_root} does not exist.")
        return
    
    # Count files for progress reporting
    total_files = sum(1 for _ in media_dir.glob('**/*') if _.is_file())
    print(f"Found {total_files} files to upload.")
    
    # Upload files
    uploaded = 0
    failed = 0
    
    for file_path in media_dir.glob('**/*'):
        if file_path.is_file():
            # Calculate the relative path from MEDIA_ROOT
            relative_path = file_path.relative_to(media_dir)
            s3_path = str(relative_path)
            
            if upload_file_to_s3(str(file_path), s3_path):
                uploaded += 1
            else:
                failed += 1
            
            # Print progress
            if (uploaded + failed) % 10 == 0 or (uploaded + failed) == total_files:
                print(f"Progress: {uploaded + failed}/{total_files} files processed.")
    
    print(f"Migration complete. {uploaded} files uploaded, {failed} files failed.")

if __name__ == "__main__":
    # Ask for confirmation
    confirm = input("This will upload all media files to S3. Continue? [y/N]: ")
    if confirm.lower() != 'y':
        print("Migration aborted.")
        sys.exit(0)
    
    migrate_media_to_s3()
    print("Media migration completed.")
