"""Audit logs page"""

import streamlit as st
import sys
import os

# Add parent directory to path to import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_bearer_token, get_user_info, init_session_state, is_authenticated
from config import AppConfig
from api_client import APIClient
from datetime import datetime

# Initialize session state if not already done
if 'initialized' not in st.session_state:
    init_session_state()
    
    # Load configuration
    config = AppConfig.from_env()
    st.session_state.config = config
    
    # Initialize clients
    from auth import CognitoAuth
    if 'auth_client' not in st.session_state:
        st.session_state.auth_client = CognitoAuth(
            user_pool_id=config.cognito_user_pool_id,
            client_id=config.cognito_client_id,
            region=config.cognito_region
        )
    
    if 'api_client' not in st.session_state:
        st.session_state.api_client = APIClient(base_url=config.api_gateway_url)
    
    st.session_state.initialized = True


def format_timestamp(timestamp_str: str) -> str:
    """Format ISO timestamp to readable format"""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return timestamp_str


def show():
    """Display audit logs page"""
    # Check authentication when accessed as standalone page
    if not is_authenticated():
        st.error("Please sign in to view audit logs")
        st.info("Go to the main app page to sign in")
        return
    
    st.title("📊 Audit Logs")
    
    access_token = get_bearer_token()
    user_info = get_user_info()
    api_client = st.session_state.api_client
    
    if not access_token or not user_info:
        st.error("Authentication required")
        return
    
    st.write("View document operation history and audit trail")
    
    # Load user's documents
    with st.spinner("Loading documents..."):
        success, list_response, error = api_client.list_documents(
            access_token=access_token
        )
    
    if not success:
        st.error(f"❌ Error loading documents: {error}")
        return
    
    if not list_response or not list_response.documents:
        st.info("📭 No documents found")
        return
    
    # Document selection
    documents = list_response.documents
    document_options = {
        f"{doc.filename} ({doc.document_id[:8]}...)": doc.document_id 
        for doc in documents
    }
    
    # Add "All Documents" option for admins
    if user_info.role == "Admin":
        document_options = {"All Documents": "all", **document_options}
    
    selected_doc_label = st.selectbox(
        "Select document to view audit logs",
        options=list(document_options.keys())
    )
    
    document_id = document_options[selected_doc_label]
    
    st.divider()
    
    # Load audit logs
    if document_id == "all":
        st.info("📋 Viewing audit logs for all documents (Admin view)")
        show_all_audit_logs(documents, access_token, api_client)
    else:
        show_document_audit_logs(document_id, selected_doc_label, access_token, api_client)
    
    # Audit log information
    st.divider()
    st.subheader("ℹ️ About Audit Logs")
    st.markdown("""
    Audit logs track all document operations including:
    - **Upload**: Document creation and upload events
    - **Download**: Document access and download events
    - **Delete**: Document deletion events
    - **Share**: Permission changes and sharing events
    - **Access Denied**: Unauthorized access attempts
    
    All logs include:
    - Timestamp of the operation
    - User who performed the action
    - Action type and result
    - Additional context and details
    """)


def show_document_audit_logs(document_id: str, document_label: str, access_token: str, api_client):
    """Display audit logs for a specific document"""
    
    st.subheader(f"Audit Trail: {document_label}")
    
    with st.spinner("Loading audit logs..."):
        success, audit_logs, error = api_client.get_audit_logs(
            access_token=access_token,
            document_id=document_id
        )
    
    if not success:
        st.error(f"❌ Error loading audit logs: {error}")
        st.info("Note: Audit log endpoint may not be fully implemented yet")
        return
    
    if not audit_logs:
        st.info("📭 No audit logs found for this document")
        return
    
    # Display audit logs
    st.caption(f"Showing {len(audit_logs)} log entries")
    
    for log in audit_logs:
        display_audit_log_entry(log)


def show_all_audit_logs(documents: list, access_token: str, api_client):
    """Display audit logs for all documents (Admin only)"""
    
    all_logs = []
    
    with st.spinner("Loading audit logs for all documents..."):
        for doc in documents:
            success, audit_logs, error = api_client.get_audit_logs(
                access_token=access_token,
                document_id=doc.document_id
            )
            
            if success and audit_logs:
                for log in audit_logs:
                    log['document_filename'] = doc.filename
                    all_logs.append(log)
    
    if not all_logs:
        st.info("📭 No audit logs found")
        st.info("Note: Audit log endpoint may not be fully implemented yet")
        return
    
    # Sort by timestamp (most recent first)
    all_logs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    st.caption(f"Showing {len(all_logs)} log entries across all documents")
    
    for log in all_logs:
        display_audit_log_entry(log)


def display_audit_log_entry(log: dict):
    """Display a single audit log entry"""
    
    # Determine icon and color based on action
    action = log.get('action', 'unknown')
    result = log.get('result', 'unknown')
    
    if result == 'success':
        icon = "✅"
        color = "green"
    elif result == 'denied':
        icon = "🚫"
        color = "red"
    elif result == 'failure':
        icon = "❌"
        color = "orange"
    else:
        icon = "ℹ️"
        color = "blue"
    
    # Action icons
    action_icons = {
        'upload': '⬆️',
        'download': '⬇️',
        'delete': '🗑️',
        'share': '👥',
        'access_denied': '🚫',
        'list': '📋'
    }
    action_icon = action_icons.get(action, '📄')
    
    with st.container():
        col1, col2, col3 = st.columns([1, 3, 2])
        
        with col1:
            st.markdown(f"### {icon}")
        
        with col2:
            st.markdown(f"**{action_icon} {action.upper()}**")
            st.caption(f"User: {log.get('user_id', 'Unknown')[:30]}...")
            if log.get('document_filename'):
                st.caption(f"Document: {log['document_filename']}")
        
        with col3:
            timestamp = log.get('timestamp', '')
            st.text(format_timestamp(timestamp))
            st.caption(f"Result: {result}")
        
        # Show details in expander
        if log.get('details'):
            with st.expander("View Details"):
                st.json(log['details'])
        
        st.divider()


def get_mock_audit_logs(document_id: str) -> list:
    """
    Generate mock audit logs for demonstration
    This is a fallback when the API endpoint is not available
    """
    from datetime import datetime, timedelta
    
    now = datetime.now()
    
    return [
        {
            'timestamp': (now - timedelta(hours=2)).isoformat(),
            'user_id': 'user@example.com',
            'action': 'upload',
            'document_id': document_id,
            'result': 'success',
            'details': {
                'filename': 'document.pdf',
                'size_bytes': 1024000,
                'ip_address': '192.168.1.1'
            }
        },
        {
            'timestamp': (now - timedelta(hours=1)).isoformat(),
            'user_id': 'viewer@example.com',
            'action': 'download',
            'document_id': document_id,
            'result': 'success',
            'details': {
                'ip_address': '192.168.1.2'
            }
        },
        {
            'timestamp': (now - timedelta(minutes=30)).isoformat(),
            'user_id': 'unauthorized@example.com',
            'action': 'download',
            'document_id': document_id,
            'result': 'denied',
            'details': {
                'reason': 'Insufficient permissions',
                'ip_address': '192.168.1.3'
            }
        }
    ]


# When accessed as a standalone Streamlit page, automatically call show()
# Only call show() if not being imported by app.py (which calls show() itself)
if 'current_page' not in st.session_state or st.session_state.get('current_page') != 'audit':
    # This page is being accessed directly via Streamlit navigation
    show()
