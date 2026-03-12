#!/bin/bash

set -e

REGION=${1:-$(aws configure get region)}
ACCOUNT_ID=${2:-$(aws sts get-caller-identity --query Account --output text)}

# Get ECR repository URI from CDK outputs
ECR_REPO_URI=$(aws cloudformation describe-stacks \
    --stack-name DocumentManagement-Storage \
    --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUri'].OutputValue" \
    --output text \
    --region $REGION)

if [ -z "$ECR_REPO_URI" ]; then
    echo "Error: Could not find ECR repository URI in CloudFormation outputs"
    echo "Make sure the CDK stack is deployed first: cd infrastructure && cdk deploy"
    exit 1
fi

echo "Building Docker image for Streamlit application..."
docker buildx build --platform linux/amd64 -t streamlit-app:latest app/

echo "Tagging Docker image..."
docker tag streamlit-app:latest $ECR_REPO_URI:latest

echo "Logging in to ECR..."
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REPO_URI

echo "Pushing Docker image to ECR..."
docker push $ECR_REPO_URI:latest

echo "Docker image pushed successfully!"
echo "ECR Repository URI: $ECR_REPO_URI:latest"
echo ""
echo "The ECS service will automatically pull the new image on next deployment."
echo "To force update the service, run:"
echo "aws ecs update-service --cluster document-management-cluster --service streamlit-service --force-new-deployment --region $REGION"
