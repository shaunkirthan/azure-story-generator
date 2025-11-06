from fastapi import FastAPI, Request
import os, requests, base64
from dotenv import load_dotenv
import uvicorn

# ---------------------------
# Load environment variables
# ---------------------------
load_dotenv()

app = FastAPI()

# ---------------------------
# Azure DevOps Setup
# ---------------------------
AZURE_ORG_URL = os.getenv("AZURE_ORG_URL")
AZURE_PROJECT = os.getenv("AZURE_PROJECT")
AZURE_TOKEN = os.getenv("AZURE_TOKEN")

# Ensure org URL doesn't have trailing slash
AZURE_ORG_URL = AZURE_ORG_URL.rstrip('/')

AZURE_WORKITEM_URL = f"{AZURE_ORG_URL}/{AZURE_PROJECT}/_apis/wit/workitems/$Issue?api-version=7.0"
print(f"✅ Azure endpoint: {AZURE_WORKITEM_URL}")

# ---------------------------
# Helper function to get Azure headers
# ---------------------------
def get_azure_headers():
    """
    Creates proper authentication headers for Azure DevOps API.
    """
    pat_token = os.getenv("AZURE_TOKEN")
    auth_string = f":{pat_token}"
    basic_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
    
    return {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/json-patch+json"
    }

# ---------------------------
# Check Azure Auth validity
# ---------------------------
def check_azure_auth():
    try:
        headers = get_azure_headers()
        headers_for_get = {"Authorization": headers["Authorization"]}
        
        test_url = f"{AZURE_ORG_URL}/_apis/projects?api-version=7.0"
        print(f"🔍 Testing auth at: {test_url}")
        
        res = requests.get(test_url, headers=headers_for_get, allow_redirects=False)
        
        if res.status_code == 200:
            print("🔐 Azure DevOps Authentication: OK ✅")
            projects = res.json()
            print(f"📋 Found {projects.get('count', 0)} projects")
            return True
        else:
            print(f"⚠️ Azure Auth Check failed: {res.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Auth check error: {str(e)}")
        return False

check_azure_auth()

# ---------------------------
# Fetch Azure Wiki Page(s)
# ---------------------------
@app.post("/tools/fetch_wiki/run")
async def fetch_wiki(request: Request):
    """
    Fetches one or multiple wiki pages from Azure DevOps Wiki
    Args:
        page_paths: list of wiki page paths (e.g., ["Wallet-Setup-And-Topup", "Wallet-Payments"])
    """
    data = await request.json()
    page_paths = data.get("args", {}).get("page_paths", [])

    if not page_paths:
        return {"result": "No page paths provided."}

    headers = get_azure_headers()
    headers["Content-Type"] = "application/json"
    
    combined_content = []
    
    for page_path in page_paths:
        wiki_url = f"{AZURE_ORG_URL}/{AZURE_PROJECT}/_apis/wiki/wikis/{AZURE_PROJECT}.wiki/pages?path=/{page_path}&includeContent=true&api-version=7.0"
        
        print(f"🔍 Fetching wiki page: {page_path}")
        print(f"🔗 URL: {wiki_url}")
        
        try:
            res = requests.get(wiki_url, headers=headers)
            print(f"📄 Wiki response status: {res.status_code}")
            
            if res.status_code == 200:
                result = res.json()
                content = result.get("content", "")
                combined_content.append(f"=== {page_path} ===\n{content}\n")
                print(f"✅ Fetched {page_path} ({len(content)} chars)")
            else:
                print(f"⚠️ Failed to fetch {page_path}: {res.status_code}")
                combined_content.append(f"=== {page_path} ===\nError: Could not fetch page\n")
                
        except Exception as e:
            print(f"❌ Error fetching {page_path}: {str(e)}")
            combined_content.append(f"=== {page_path} ===\nError: {str(e)}\n")
    
    return {"result": "\n".join(combined_content)}

# ---------------------------
# Create Issue in Azure Boards
# ---------------------------
@app.post("/tools/create_story/run")
async def create_story(request: Request):
    data = await request.json()
    title = data.get("args", {}).get("title", "")
    description = data.get("args", {}).get("description", "")
    epic_id = data.get("args", {}).get("epic_id", None)  # Optional: link to epic

    if not title or not description:
        return {"result": "Missing title or description."}

    # Clean up title
    clean_title = title.strip()
    clean_title = clean_title.replace("###", "").replace("##", "").replace("#", "")
    clean_title = clean_title.replace("**", "")
    clean_title = " ".join(clean_title.split())
    
    # Skip if title is acceptance criteria or separator
    skip_keywords = ["acceptance criteria", "---", "as a", "**as"]
    if any(keyword in clean_title.lower()[:30] for keyword in skip_keywords):
        print(f"⏭️ Skipping non-story item: {clean_title[:50]}")
        return {"result": f"Skipped: {clean_title[:50]}"}
    
    if len(clean_title) < 10:
        print(f"⏭️ Skipping short title: {clean_title}")
        return {"result": f"Skipped short title: {clean_title}"}

    # Format description with proper line breaks and bullets
    clean_description = description.strip()
    formatted_description = clean_description.replace("\n", "<br/>")
    formatted_description = formatted_description.replace("<br/>- ", "<br/>• ")
    formatted_description = formatted_description.replace("<br/>* ", "<br/>• ")

    # Create work item payload
    payload = [
        {"op": "add", "path": "/fields/System.Title", "value": clean_title[:255]},
        {"op": "add", "path": "/fields/System.Description", "value": formatted_description},
    ]
    
    # Link to epic if provided
    if epic_id:
        payload.append({
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": f"{AZURE_ORG_URL}/{AZURE_PROJECT}/_apis/wit/workItems/{epic_id}"
            }
        })

    headers = get_azure_headers()

    print(f"📦 Creating issue: {clean_title[:60]}...")
    print(f"🔗 Posting to: {AZURE_WORKITEM_URL}")

    try:
        r = requests.post(AZURE_WORKITEM_URL, json=payload, headers=headers, allow_redirects=False)
        
        print(f"📤 Azure Response Status: {r.status_code}")
        
        if r.status_code == 200:
            result = r.json()
            work_item_id = result.get('id')
            print(f"✅ Successfully created issue #{work_item_id}")
            return {
                "result": f"Successfully created Issue #{work_item_id}: {clean_title[:50]}",
                "id": work_item_id,
                "url": result.get('_links', {}).get('html', {}).get('href', '')
            }
        else:
            print(f"⚠️ Unexpected response: {r.status_code}")
            print(f"📄 Response Body:\n{r.text[:1000]}")
            return {"result": f"Error {r.status_code}: {r.text[:500]}"}
            
    except Exception as e:
        print(f"❌ Exception occurred: {str(e)}")
        return {"result": f"Error posting to Azure Boards: {str(e)}"}

# ---------------------------
# Health check endpoint
# ---------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "azure_org": AZURE_ORG_URL,
        "azure_project": AZURE_PROJECT
    }

@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "MCP Server for Azure DevOps"
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    uvicorn.run(app, host="0.0.0.0", port=port)
