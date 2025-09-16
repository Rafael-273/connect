#!/usr/bin/env python
"""
Script to check S3 connection and verify that the configuration is working correctly.
This script should be run after S3 is configured in settings.py.

Usage:
    python check_s3_connection.py

Requirements:
    - Django settings should be configured with AWS_STORAGE_BUCKET_NAME and other AWS settings
    - boto3 and django-storages should be installed
"""

import os
import sys
import django
from django.conf import settings
import boto3
from botocore.exceptions import ClientError

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "connect.settings")
django.setup()

def check_s3_connection():
    """Check if we can connect to S3 and the bucket exists."""
    # Check if S3 is configured
    if not getattr(settings, 'USE_S3', False):
        print("❌ S3 is not configured in settings. Please set USE_S3=TRUE in .env file.")
        return False

    # Get AWS credentials from settings
    aws_access_key_id = getattr(settings, 'AWS_ACCESS_KEY_ID', None)
    aws_secret_access_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
    aws_bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)

    if not all([aws_access_key_id, aws_secret_access_key, aws_bucket_name]):
        print("❌ AWS credentials are not properly configured in settings.")
        print(f"AWS_ACCESS_KEY_ID: {'✓' if aws_access_key_id else '❌'}")
        print(f"AWS_SECRET_ACCESS_KEY: {'✓' if aws_secret_access_key else '❌'}")
        print(f"AWS_STORAGE_BUCKET_NAME: {'✓' if aws_bucket_name else '❌'}")
        return False

    # Create S3 client
    try:
        print("🔄 Connecting to AWS S3...")
        s3 = boto3.client(
            's3',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key
        )
        
        # List buckets to check connection
        response = s3.list_buckets()
        print(f"✅ Successfully connected to AWS S3. Found {len(response['Buckets'])} buckets.")
        
        # Check if our bucket exists
        bucket_exists = False
        for bucket in response['Buckets']:
            if bucket['Name'] == aws_bucket_name:
                bucket_exists = True
                break
        
        if bucket_exists:
            print(f"✅ Bucket '{aws_bucket_name}' exists.")
        else:
            print(f"❌ Bucket '{aws_bucket_name}' does not exist. Please create it first.")
            return False
        
        # Check if we can write to the bucket
        print("🔄 Testing write access to bucket...")
        test_key = 'media/test-file.txt'
        try:
            s3.put_object(
                Bucket=aws_bucket_name,
                Key=test_key,
                Body='This is a test file to check if we can write to the bucket.'
            )
            print(f"✅ Successfully wrote test file to '{aws_bucket_name}/{test_key}'.")
            
            # Clean up test file
            s3.delete_object(
                Bucket=aws_bucket_name,
                Key=test_key
            )
            print(f"✅ Successfully deleted test file from '{aws_bucket_name}/{test_key}'.")
            return True
        except ClientError as e:
            print(f"❌ Error writing to bucket: {e}")
            return False
            
    except Exception as e:
        print(f"❌ Error connecting to AWS S3: {e}")
        return False

if __name__ == "__main__":
    print("=== S3 Connection Check ===")
    success = check_s3_connection()
    if success:
        print("\n✅ S3 configuration is working correctly!")
        print("You can now use S3 for media storage.")
    else:
        print("\n❌ S3 configuration check failed.")
        print("Please check your AWS credentials and bucket configuration.")
