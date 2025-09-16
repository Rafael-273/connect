#!/bin/bash
# setup_s3.sh - Script to help set up S3 storage for Connect platform

echo "====== Connect Platform - S3 Storage Setup ======"
echo ""

# Check for dependencies
command -v aws >/dev/null 2>&1 || { 
    echo "❌ AWS CLI is required but not installed. Please install it first:"
    echo "   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
}

echo "This script will help you set up AWS S3 storage for media files."
echo "Make sure you have AWS credentials with permissions to create S3 buckets."
echo ""

# Check if AWS CLI is configured
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "❌ AWS CLI is not configured. Please run 'aws configure' first."
    exit 1
fi

echo "✅ AWS CLI is configured."
echo ""

# Ask for bucket name
read -p "Enter a unique name for your S3 bucket: " bucket_name
if [ -z "$bucket_name" ]; then
    echo "❌ Bucket name cannot be empty."
    exit 1
fi

# Check if bucket already exists
if aws s3api head-bucket --bucket "$bucket_name" 2>/dev/null; then
    echo "⚠️ Bucket '$bucket_name' already exists."
    read -p "Do you want to use this existing bucket? (y/n): " use_existing
    if [[ $use_existing != "y" && $use_existing != "Y" ]]; then
        echo "Operation cancelled."
        exit 1
    fi
else
    echo "🔄 Creating bucket '$bucket_name'..."
    
    # Get AWS region
    region=$(aws configure get region)
    if [ -z "$region" ]; then
        region="us-east-1"
    fi
    
    # Create the bucket
    if [ "$region" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$bucket_name" --region "$region"
    else
        aws s3api create-bucket --bucket "$bucket_name" --region "$region" --create-bucket-configuration LocationConstraint="$region"
    fi
    
    if [ $? -ne 0 ]; then
        echo "❌ Failed to create bucket. Please check your AWS permissions or try a different bucket name."
        exit 1
    fi
    
    echo "✅ Bucket created successfully."
fi

# Configure bucket for public read access
echo "🔄 Configuring bucket for public read access..."
cat > /tmp/bucket-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadForGetBucketObjects",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::$bucket_name/*"
        }
    ]
}
EOF

aws s3api put-bucket-policy --bucket "$bucket_name" --policy file:///tmp/bucket-policy.json
if [ $? -ne 0 ]; then
    echo "❌ Failed to set bucket policy. You may need to configure public access manually."
else
    echo "✅ Bucket policy set successfully."
fi

# Get AWS credentials
echo ""
echo "To use this bucket in Connect, you need AWS credentials."
echo "You can either use your current AWS credentials or create new ones."
echo ""
read -p "Do you want to use your current AWS credentials? (y/n): " use_current_creds

if [[ $use_current_creds == "y" || $use_current_creds == "Y" ]]; then
    # Get current credentials
    access_key=$(aws configure get aws_access_key_id)
    secret_key=$(aws configure get aws_secret_access_key)
    
    if [ -z "$access_key" ] || [ -z "$secret_key" ]; then
        echo "❌ Could not retrieve AWS credentials. Please check your AWS configuration."
        exit 1
    fi
else
    echo "Please create an IAM user with S3 access permissions and enter the credentials:"
    read -p "AWS Access Key ID: " access_key
    read -p "AWS Secret Access Key: " secret_key
    
    if [ -z "$access_key" ] || [ -z "$secret_key" ]; then
        echo "❌ Both Access Key ID and Secret Access Key are required."
        exit 1
    fi
fi

# Update .env file
echo ""
echo "🔄 Updating .env file with S3 configuration..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ .env file not found. Please create it first."
    exit 1
fi

# Update .env file
grep -q "^USE_S3=" .env && sed -i '' "s/^USE_S3=.*/USE_S3=TRUE/" .env || echo "USE_S3=TRUE" >> .env
grep -q "^AWS_ACCESS_KEY_ID=" .env && sed -i '' "s/^AWS_ACCESS_KEY_ID=.*/AWS_ACCESS_KEY_ID=$access_key/" .env || echo "AWS_ACCESS_KEY_ID=$access_key" >> .env
grep -q "^AWS_SECRET_ACCESS_KEY=" .env && sed -i '' "s/^AWS_SECRET_ACCESS_KEY=.*/AWS_SECRET_ACCESS_KEY=$secret_key/" .env || echo "AWS_SECRET_ACCESS_KEY=$secret_key" >> .env
grep -q "^AWS_STORAGE_BUCKET_NAME=" .env && sed -i '' "s/^AWS_STORAGE_BUCKET_NAME=.*/AWS_STORAGE_BUCKET_NAME=$bucket_name/" .env || echo "AWS_STORAGE_BUCKET_NAME=$bucket_name" >> .env

echo "✅ .env file updated successfully."

# Install required packages
echo ""
echo "🔄 Making sure required packages are in requirements.txt..."

# Check if django-storages and boto3 are in requirements.txt
if ! grep -q "django-storages" requirements.txt; then
    echo "django-storages==1.14.2" >> requirements.txt
    echo "✅ Added django-storages to requirements.txt"
fi

if ! grep -q "boto3" requirements.txt; then
    echo "boto3==1.34.124" >> requirements.txt
    echo "✅ Added boto3 to requirements.txt"
fi

echo ""
echo "✅ S3 setup complete! To apply these changes:"
echo "   1. Rebuild and restart your Docker containers:"
echo "      docker-compose down && docker-compose up -d"
echo ""
echo "   2. To migrate existing media files to S3, run:"
echo "      python migrate_to_s3.py"
echo ""
echo "   3. To verify S3 configuration, run:"
echo "      python check_s3_connection.py"
echo ""
echo "For more information, see MEDIA_STORAGE.md"
