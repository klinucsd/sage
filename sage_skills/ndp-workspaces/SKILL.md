---
name: ndp-workspaces
description: Load and manage workspace data from the NDP Workspace API using direct HTTP calls
---

# NDP Workspaces Skill

## Description

This skill provides functionality to load workspace data from the NDP Workspace API using direct HTTP requests via curl. It's designed to integrate with JupyterHub environments and retrieve workspace configurations for entities. The skill reads authentication credentials from environment variables and makes API calls to fetch workspace information.

The Swagger UI for the workspace API is at https://nationaldataplatform.org/workspaces-api/v1/openapi.json

## When to Use

- When the user needs to retrieve workspace configurations from the NDP API
- When setting up or configuring JupyterHub environments based on workspace data
- When filtering workspaces by specific entity IDs
- When you need to automate workspace data retrieval for downstream processes
- When troubleshooting workspace access or configuration issues

## Prerequisites

The following environment variables must be set:
- `WORKSPACE_API_URL` - Base URL for the NDP Workspace API
- `ACCESS_TOKEN` - Bearer token for authentication

Optional environment variable:
- `ENTITY_ID` - Entity ID to filter workspaces (if not provided, all accessible workspaces are returned)

## How to Use

### Step 1: Verify Environment Variables

Check that the required environment variables are set:
```bash
echo "API URL: $WORKSPACE_API_URL"
echo "Token present: $([ -n "$ACCESS_TOKEN" ] && echo "Yes" || echo "No")"
```

### Step 2: Make the API Call

Use curl to call the NDP Workspace API endpoint. The basic pattern is:

```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub"
```

### Step 3: Process the Results

The API returns JSON data containing workspace information. You can:
- Pipe the output to `jq` for parsing and filtering
- Save it to a file for later processing
- Use it directly in your automation workflow

## API Endpoint

**Endpoint:**
```
GET {WORKSPACE_API_URL}/workspace/read_workspaces_for_jupyterhub
```

**Headers:**
- `Authorization: Bearer {ACCESS_TOKEN}`

**Query Parameters:**
- `entity_id` (optional): Filter workspaces by entity ID

## Best Practices

- **Security**: Never hardcode access tokens. Always use environment variables
- **Error Handling**: Check HTTP response codes. Use `-f` flag with curl to fail on HTTP errors
- **Entity Filtering**: Use the `entity_id` parameter to filter workspaces when you only need data for specific entities
- **Output Management**: Use `-o` flag to save responses to files for batch operations
- **JSON Processing**: Use `jq` for parsing and manipulating JSON responses
- **Debugging**: Add `-v` flag to curl for verbose output when troubleshooting

## Examples

### Example 1: Basic Workspace Retrieval

**User Request:** "Fetch all workspaces from the NDP API"

**Approach:**
1. Verify environment variables are set
2. Make a GET request to the workspaces endpoint
3. Pretty-print the JSON output

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | jq '.'
```

**Expected outcome:** JSON output with all workspaces the token has access to, formatted with jq

### Example 2: Entity-Specific Workspace Retrieval

**User Request:** "Get workspaces for entity ID 'project-alpha' and save to a file"

**Approach:**
1. Add the entity_id query parameter to the request
2. Save the output to a JSON file
3. Verify the file was created successfully

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub?entity_id=project-alpha" \
  -o project_alpha_workspaces.json

cat project_alpha_workspaces.json | jq '.'
```

**Expected result:** A JSON file containing only workspaces associated with 'project-alpha'

### Example 3: Using Environment Variable for Entity ID

**User Request:** "Retrieve workspaces for the entity specified in ENTITY_ID environment variable"

**Approach:**
1. Use the ENTITY_ID environment variable if set
2. Handle the case where ENTITY_ID might not be set
3. Save and display results

```bash
if [ -n "$ENTITY_ID" ]; then
  curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub?entity_id=$ENTITY_ID" \
    -o workspaces.json
else
  curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" \
    -o workspaces.json
fi

echo "Workspaces saved to workspaces.json"
cat workspaces.json | jq '.'
```

**Expected result:** Workspaces filtered by ENTITY_ID if set, otherwise all workspaces

### Example 4: Error Handling and Status Checking

**User Request:** "Retrieve workspaces with proper error handling"

**Approach:**
1. Use curl with fail flag and capture HTTP status
2. Check for errors and display appropriate messages
3. Only process response if successful

```bash
HTTP_CODE=$(curl -s -w "%{http_code}" -o workspaces.json \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub")

if [ "$HTTP_CODE" -eq 200 ]; then
  echo "Success! Workspaces retrieved."
  cat workspaces.json | jq '.'
else
  echo "Error: HTTP $HTTP_CODE"
  cat workspaces.json
  exit 1
fi
```

**Expected result:** Graceful error handling with appropriate status messages

### Example 5: Extracting Specific Fields

**User Request:** "Get just the workspace names and IDs"

**Approach:**
1. Fetch the workspaces data
2. Use jq to extract only the required fields
3. Format as a simple list

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq -r '.workspaces[] | "\(.id): \(.name)"'
```

**Expected result:** A clean list of workspace IDs and names

## Common jq Patterns

**Count workspaces:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq '.workspaces | length'
```

**Filter workspaces by a field:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq '.workspaces[] | select(.status == "active")'
```

**Extract to CSV:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq -r '.workspaces[] | [.id, .name, .status] | @csv'
```

## Troubleshooting

### Common Issues

**Authentication Errors (401):**
- Verify your ACCESS_TOKEN environment variable is set correctly
- Check that the token is valid and not expired
- Ensure the token has the necessary permissions

**Connection Errors:**
- Verify WORKSPACE_API_URL is correct and accessible
- Check network connectivity
- Test with: `curl -v $WORKSPACE_API_URL`

**Empty or Missing Environment Variables:**
```bash
# Check if variables are set
if [ -z "$WORKSPACE_API_URL" ]; then
  echo "Error: WORKSPACE_API_URL not set"
  exit 1
fi

if [ -z "$ACCESS_TOKEN" ]; then
  echo "Error: ACCESS_TOKEN not set"
  exit 1
fi
```

**No Workspaces Returned:**
- Verify the entity ID is correct (if filtering)
- Check that the token has access to the requested workspaces
- Review API permissions

## Notes

- All authentication is handled via the Authorization header with Bearer token
- The API endpoint is specifically designed for JupyterHub integration
- Response data structure depends on the API implementation; use `jq` to explore the structure
- For automated workflows, consider adding retry logic for transient network failures
- Use `-k` flag with curl to skip SSL verification in development (not recommended for production)