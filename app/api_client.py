"""API client for Document Management System"""

import requests
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import base64


@dataclass
class DocumentMetadata:
    """Document metadata"""
    document_id: str
    filename: str
    owner: str
    department: str
    upload_timestamp: str
    size_bytes: int
    content_type: str
    shared_with: List[str]
    tags: List[str]
    last_modified: Optional[str] = None  # Optional field for last modified timestamp


@dataclass
class UploadResponse:
    """Document upload response"""
    presigned_url: str
    document_id: str
    s3_key: str
    expires_at: str
    content_type: Optional[str] = None  # Content type used for presigned URL


@dataclass
class DownloadResponse:
    """Document download response"""
    presigned_url: str
    expires_at: str


@dataclass
class ListResponse:
    """Document list response"""
    documents: List[DocumentMetadata]
    next_token: Optional[str] = None


class APIClient:
    """Client for Document Management API"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
    
    def _get_headers(self, access_token: str) -> Dict[str, str]:
        """Get request headers with authentication"""
        return {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    
    def _handle_response(self, response: requests.Response) -> tuple[bool, Optional[Any], Optional[str]]:
        """Handle API response"""
        try:
            if response.status_code == 200:
                return True, response.json(), None
            elif response.status_code == 401:
                return False, None, "Unauthorized - please sign in again"
            elif response.status_code == 403:
                return False, None, "Permission denied"
            elif response.status_code == 404:
                return False, None, "Resource not found"
            elif response.status_code == 429:
                return False, None, "Rate limit exceeded - please try again later"
            elif response.status_code == 503:
                return False, None, "Service temporarily unavailable"
            else:
                # Try to parse error message from response
                error_msg = f'HTTP {response.status_code}'
                try:
                    if response.content:
                        error_data = response.json()
                        # Try multiple error message formats
                        if isinstance(error_data, dict):
                            # Try different error message locations (check for None explicitly)
                            message = error_data.get('message')
                            if message is not None and message != '':
                                error_msg = str(message)
                            elif isinstance(error_data.get('error'), dict):
                                error_msg_obj = error_data['error']
                                if error_msg_obj.get('message'):
                                    error_msg = str(error_msg_obj['message'])
                                else:
                                    import json
                                    error_msg = f'HTTP {response.status_code}: {json.dumps(error_data, indent=2)}'
                            elif isinstance(error_data.get('error'), str):
                                error_msg = error_data['error']
                            elif error_data.get('errorMessage'):
                                error_msg = str(error_data['errorMessage'])
                            else:
                                # Fallback: show the full error data as JSON string for debugging
                                import json
                                error_msg = f'HTTP {response.status_code}: {json.dumps(error_data, indent=2)}'
                        else:
                            error_msg = str(error_data)
                except Exception as parse_error:
                    # If JSON parsing fails, use raw text
                    error_msg = response.text[:500] if response.text else f'HTTP {response.status_code}'
                
                return False, None, error_msg
        except Exception as e:
            return False, None, f"Error processing response: {str(e)}"
    
    def upload_document(
        self,
        access_token: str,
        filename: str,
        content_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, Optional[UploadResponse], Optional[str]]:
        """
        Initiate document upload and get pre-signed URL
        
        Returns:
            (success, upload_response, error_message)
        """
        try:
            url = f"{self.base_url}/documents"
            headers = self._get_headers(access_token)
            
            payload = {
                'filename': filename,
                'content_type': content_type,
                'metadata': metadata or {}
            }
            
            # Debug: Log request details (without sensitive token)
            import streamlit as st
            if hasattr(st, 'session_state') and st.session_state.get('debug_mode', False):
                st.write(f"**Debug:** POST {url}")
                st.write(f"**Headers:** {list(headers.keys())}")
                st.write(f"**Payload:** {payload}")
            
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            
            # Debug: Log response details
            if hasattr(st, 'session_state') and st.session_state.get('debug_mode', False):
                st.write(f"**Response Status:** {response.status_code}")
                st.write(f"**Response Body:** {response.text[:500]}")
            
            success, data, error = self._handle_response(response)
            
            if success and data:
                # Calculate expires_at from expires_in if expires_at is not present
                expires_at = data.get('expires_at')
                if not expires_at and data.get('expires_in'):
                    from datetime import datetime, timedelta
                    expires_at = (datetime.utcnow() + timedelta(seconds=data['expires_in'])).isoformat() + 'Z'
                
                # Get content_type from response if available, otherwise use the one from upload_instructions
                response_content_type = data.get('content_type')
                if not response_content_type and 'upload_instructions' in data:
                    response_content_type = data['upload_instructions'].get('headers', {}).get('Content-Type')
                
                upload_resp = UploadResponse(
                    presigned_url=data['presigned_url'],
                    document_id=data['document_id'],
                    s3_key=data['s3_key'],
                    expires_at=expires_at or '',  # Fallback to empty string if neither is present
                    content_type=response_content_type
                )
                return True, upload_resp, None
            
            return False, None, error
            
        except requests.exceptions.Timeout:
            return False, None, "Request timeout - please try again"
        except Exception as e:
            return False, None, f"Upload error: {str(e)}"
    
    def upload_to_s3(self, presigned_url: str, file_content: bytes, content_type: str) -> tuple[bool, Optional[str]]:
        """
        Upload file content to S3 using pre-signed URL
        
        Returns:
            (success, error_message)
        """
        try:
            # S3 presigned URLs require exact Content-Type match
            # Ensure content_type is set, default to application/octet-stream if not provided
            if not content_type:
                content_type = 'application/octet-stream'
            
            headers = {
                'Content-Type': content_type
            }
            
            # Debug: Log upload details (without sensitive URL)
            import streamlit as st
            if hasattr(st, 'session_state') and st.session_state.get('debug_mode', False):
                st.write(f"**S3 Upload Debug:**")
                st.write(f"**Content-Type:** {content_type}")
                st.write(f"**File Size:** {len(file_content)} bytes")
                st.write(f"**URL (first 50 chars):** {presigned_url[:50]}...")
            
            response = requests.put(
                presigned_url, 
                data=file_content, 
                headers=headers, 
                timeout=300,
                allow_redirects=False  # Don't follow redirects for S3
            )
            
            if response.status_code == 200:
                return True, None
            else:
                # Try to extract error message from S3 response
                error_msg = f"S3 upload failed: {response.status_code}"
                try:
                    if response.text:
                        # S3 errors are usually XML
                        error_details = response.text[:500]
                        error_msg = f"S3 upload failed: {response.status_code}\n\nResponse: {error_details}"
                        
                        # Check for common error patterns
                        if 'SignatureDoesNotMatch' in error_details:
                            error_msg += "\n\nPossible cause: Content-Type mismatch or expired URL"
                        elif 'AccessDenied' in error_details:
                            error_msg += "\n\nPossible cause: Bucket policy or permissions issue"
                except:
                    pass
                
                return False, error_msg
                
        except requests.exceptions.Timeout:
            return False, "Upload timeout - file may be too large"
        except Exception as e:
            return False, f"S3 upload error: {str(e)}"
    
    def list_documents(
        self,
        access_token: str,
        filters: Optional[Dict[str, Any]] = None,
        next_token: Optional[str] = None
    ) -> tuple[bool, Optional[ListResponse], Optional[str]]:
        """
        List documents accessible to user
        
        Returns:
            (success, list_response, error_message)
        """
        try:
            url = f"{self.base_url}/documents"
            headers = self._get_headers(access_token)
            
            params = {}
            if filters:
                params.update(filters)
            if next_token:
                params['next_token'] = next_token
            
            response = self.session.get(url, headers=headers, params=params, timeout=30)
            success, data, error = self._handle_response(response)
            
            if success and data:
                documents = []
                for doc in data.get('documents', []):
                    try:
                        # Only include fields that exist in DocumentMetadata dataclass
                        doc_metadata = DocumentMetadata(
                            document_id=doc.get('document_id', ''),
                            filename=doc.get('filename', ''),
                            owner=doc.get('owner', ''),
                            department=doc.get('department', ''),
                            upload_timestamp=doc.get('upload_timestamp', ''),
                            size_bytes=doc.get('size_bytes', 0),
                            content_type=doc.get('content_type', ''),
                            shared_with=doc.get('shared_with', []),
                            tags=doc.get('tags', []),
                            last_modified=doc.get('last_modified')  # Optional field
                        )
                        documents.append(doc_metadata)
                    except Exception as e:
                        # Skip documents that fail to parse
                        print(f"Error parsing document: {str(e)}, doc: {doc}")
                        continue
                
                list_resp = ListResponse(
                    documents=documents,
                    next_token=data.get('next_token')
                )
                return True, list_resp, None
            
            return False, None, error
            
        except Exception as e:
            return False, None, f"List error: {str(e)}"
    
    def download_document(
        self,
        access_token: str,
        document_id: str
    ) -> tuple[bool, Optional[DownloadResponse], Optional[str]]:
        """
        Get pre-signed URL for document download
        
        Returns:
            (success, download_response, error_message)
        """
        try:
            url = f"{self.base_url}/documents/{document_id}"
            headers = self._get_headers(access_token)
            
            response = self.session.get(url, headers=headers, timeout=30)
            success, data, error = self._handle_response(response)
            
            if success and data:
                download_resp = DownloadResponse(
                    presigned_url=data['presigned_url'],
                    expires_at=data['expires_at']
                )
                return True, download_resp, None
            
            return False, None, error
            
        except Exception as e:
            return False, None, f"Download error: {str(e)}"
    
    def delete_document(
        self,
        access_token: str,
        document_id: str
    ) -> tuple[bool, Optional[str]]:
        """
        Delete document
        
        Returns:
            (success, error_message)
        """
        try:
            url = f"{self.base_url}/documents/{document_id}"
            headers = self._get_headers(access_token)
            
            response = self.session.delete(url, headers=headers, timeout=30)
            success, _, error = self._handle_response(response)
            
            return success, error
            
        except Exception as e:
            return False, f"Delete error: {str(e)}"
    
    def share_document(
        self,
        access_token: str,
        document_id: str,
        user_id: str,
        permissions: List[str]
    ) -> tuple[bool, Optional[str]]:
        """
        Share document with user
        
        Returns:
            (success, error_message)
        """
        try:
            url = f"{self.base_url}/documents/{document_id}/share"
            headers = self._get_headers(access_token)
            
            payload = {
                'user_id': user_id,
                'permissions': permissions
            }
            
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            success, _, error = self._handle_response(response)
            
            return success, error
            
        except Exception as e:
            return False, f"Share error: {str(e)}"
    
    def get_audit_logs(
        self,
        access_token: str,
        document_id: str
    ) -> tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        Get audit logs for document
        
        Returns:
            (success, audit_logs, error_message)
        """
        try:
            url = f"{self.base_url}/documents/{document_id}/audit"
            headers = self._get_headers(access_token)
            
            response = self.session.get(url, headers=headers, timeout=30)
            success, data, error = self._handle_response(response)
            
            if success and data:
                return True, data.get('audit_logs', []), None
            
            return False, None, error
            
        except Exception as e:
            return False, None, f"Audit log error: {str(e)}"
