# Streamlit Document Management Application

Web interface for the AWS Document Management System.

## Features

- **Authentication**: Cognito-based user authentication with role-based access
- **Document Management**: Upload, download, list, and delete documents
- **Document Sharing**: Share documents with other users and manage permissions
- **Audit Logs**: View document operation history and audit trail
- **Admin Panel**: User management and system administration (Admin role only)

## Architecture

The Streamlit application provides a user-friendly web interface that:
- Authenticates users via Amazon Cognito
- Calls API Gateway endpoints for document operations
- Displays role-based UI controls based on user permissions
- Shows real-time operation feedback and status messages

## Configuration

Set the following environment variables:

```bash
export API_GATEWAY_URL="https://your-api-gateway-url.execute-api.region.amazonaws.com/prod"
export COGNITO_USER_POOL_ID="your-user-pool-id"
export COGNITO_CLIENT_ID="your-client-id"
export AWS_REGION="us-east-1"
```

## Local Development

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables (see Configuration above)

3. Run the application:
```bash
streamlit run app.py
```

4. Access at http://localhost:8501

## Docker Deployment

Build the Docker image:
```bash
docker build -t document-management-ui .
```

Run the container:
```bash
docker run -p 8501:8501 \
  -e API_GATEWAY_URL="your-api-url" \
  -e COGNITO_USER_POOL_ID="your-pool-id" \
  -e COGNITO_CLIENT_ID="your-client-id" \
  -e AWS_REGION="us-east-1" \
  document-management-ui
```

## Project Structure

```
app/
├── app.py                 # Main application entry point
├── config.py              # Configuration management
├── auth.py                # Cognito authentication
├── api_client.py          # API Gateway client
├── pages/                 # Application pages
│   ├── documents.py       # Document list page
│   ├── upload.py          # Document upload page
│   ├── share.py           # Document sharing page
│   ├── audit.py           # Audit logs page
│   └── admin.py           # Admin panel page
├── requirements.txt       # Python dependencies
├── Dockerfile            # Docker configuration
└── README.md             # This file
```

## User Roles

- **Admin**: Full system access, user management, all document operations
- **Manager**: Departmental document management, create/read/update/share
- **Editor**: Create and edit owned or shared documents
- **Viewer**: Read-only access to shared documents

## Security

- All API requests include JWT authentication tokens
- Tokens are stored in Streamlit session state
- HTTPS enforced for all API communication
- Role-based UI controls prevent unauthorized actions
- Session tokens expire after 1 hour (configurable in Cognito)

## Pages

### Login Page
- Email/password authentication
- MFA support (if enabled)
- Error handling and feedback

### Documents Page
- List all accessible documents
- Filter by owner (my documents, shared with me)
- Search by filename
- Download documents via pre-signed URLs
- Delete documents (with confirmation)

### Upload Page
- File picker with drag-and-drop
- Metadata input (department, tags, description)
- Progress tracking
- 5GB file size limit
- Real-time upload feedback

### Share Page
- Select document to share
- Enter target user ID/email
- Configure permissions (read, write, delete, share)
- View currently shared documents

### Audit Logs Page
- View document operation history
- Filter by document
- Admin view for all documents
- Detailed log entries with timestamps

### Admin Panel (Admin only)
- User management (create, disable, delete users)
- Password reset
- System statistics
- Configuration viewing

## Troubleshooting

### Configuration Errors
Ensure all environment variables are set correctly. The app will display an error message if configuration is missing.

### Authentication Errors
- Verify Cognito User Pool ID and Client ID
- Check that the user exists and is confirmed
- Ensure password meets policy requirements

### API Errors
- Verify API Gateway URL is correct
- Check that the API is deployed and accessible
- Ensure Lambda functions are working correctly

### Permission Errors
- Verify user role is set correctly in Cognito
- Check Cedar policies in Verified Permissions
- Ensure document ownership and sharing are configured

## Development Notes

- Session state is managed by Streamlit
- Authentication tokens are refreshed automatically
- API client handles retries and error responses
- All timestamps are displayed in local timezone
- File uploads use pre-signed URLs for direct S3 access
