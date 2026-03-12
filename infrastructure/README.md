# Document Management System - Infrastructure

AWS CDK infrastructure code for the Document Management System.

## Prerequisites

- Python 3.11 or later
- AWS CLI configured with appropriate credentials
- Node.js and npm (for AWS CDK CLI)

## Setup

1. Install AWS CDK CLI globally:
```bash
npm install -g aws-cdk
```

2. Create and activate Python virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Deployment

1. Bootstrap CDK (first time only):
```bash
cdk bootstrap
```

2. Synthesize CloudFormation template:
```bash
cdk synth
```

3. Deploy stack:
```bash
cdk deploy
```

4. Destroy stack (when needed):
```bash
cdk destroy
```

## Project Structure

- `app.py` - CDK application entry point
- `stacks/` - CDK stack definitions
- `cdk.json` - CDK configuration
- `requirements.txt` - Python dependencies
