// Cedar Policy: Viewer Read-Only Access
// Requirements: 2.5
// Viewer users have read-only access to documents shared with them

permit(
    principal,
    action == DocumentManagement::Action::"read",
    resource
)
when {
    principal has role &&
    principal.role == "Viewer" &&
    principal has userId &&
    resource has sharedWith &&
    resource.sharedWith.contains(principal.userId)
};
