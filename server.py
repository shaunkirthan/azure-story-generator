from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import json
import base64
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def call_openai(messages, temperature=0.7):
    """Direct HTTP call to OpenAI API"""
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-4",
        "messages": messages,
        "temperature": temperature
    }
    
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload
    )
    
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        raise Exception(f"OpenAI API error: {response.status_code}")

class FindWikiRequest(BaseModel):
    epic_title: str

class GenerateRequest(BaseModel):
    wiki_page_paths: list[str]
    epic_id: int = None

class EpicRequest(BaseModel):
    epic_id: int

# NEW ENDPOINT - Complete workflow from Epic ID
@app.post("/generate_from_epic")
async def generate_from_epic(request: EpicRequest):
    """
    Complete workflow: fetch epic title, find wiki pages, generate stories
    All from just an Epic ID
    """
    epic_id = request.epic_id
    
    if not epic_id:
        return {"error": "No epic ID provided"}
    
    print(f"🎯 Processing Epic #{epic_id}")
    
    # Step 1: Get Epic title from Azure DevOps REST API
    try:
        azure_org = os.getenv("AZURE_ORG_URL").rstrip('/')
        azure_project = os.getenv("AZURE_PROJECT")
        azure_token = os.getenv("AZURE_TOKEN")
        
        auth_string = f":{azure_token}"
        basic_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        headers = {
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/json"
        }
        
        epic_url = f"{azure_org}/{azure_project}/_apis/wit/workitems/{epic_id}?api-version=7.0"
        print(f"📡 Fetching Epic from: {epic_url}")
        
        epic_res = requests.get(epic_url, headers=headers, timeout=30)
        
        if epic_res.status_code != 200:
            return {"error": f"Failed to fetch Epic: {epic_res.status_code}"}
        
        epic_data = epic_res.json()
        epic_title = epic_data.get("fields", {}).get("System.Title", "")
        
        if not epic_title:
            return {"error": "Epic has no title"}
        
        print(f"✅ Epic title: {epic_title}")
        
    except Exception as e:
        print(f"❌ Error fetching Epic: {str(e)}")
        return {"error": f"Failed to read Epic: {str(e)}"}
    
    # Step 2: Find related wiki pages
    try:
        all_pages = await get_all_wiki_pages()
        
        if not all_pages:
            return {"error": "No wiki pages found in project", "story_count": 0}
        
        # Use AI to match pages if OpenAI key available, otherwise use keyword matching
        if os.getenv('OPENAI_API_KEY'):
            prompt = f"""You are analyzing which wiki pages are relevant to an Epic.

Epic Title: "{epic_title}"

Available Wiki Pages:
{chr(10).join(f"- {page}" for page in all_pages)}

Task: Identify which wiki pages are related to this epic and rate each match from 0.0 to 1.0.

Only include pages with confidence >= 0.6.

Response format (JSON):
{{
  "matches": [
    {{"path": "page-name", "confidence": 0.95, "reason": "why it matches"}}
  ]
}}

Response:"""

            try:
                response_text = call_openai([
                    {"role": "system", "content": "You are an expert at matching documentation to project epics."},
                    {"role": "user", "content": prompt}
                ], temperature=0.3)
                
                result = json.loads(response_text)
                matched_pages = result.get("matches", [])
                print(f"🤖 AI matched {len(matched_pages)} wiki pages")
                
            except Exception as e:
                print(f"⚠️ AI matching failed, using keyword fallback: {str(e)}")
                matched_pages = []
                epic_keywords = epic_title.lower().split()
                for page in all_pages:
                    page_lower = page.lower()
                    score = sum(1 for word in epic_keywords if word in page_lower) / len(epic_keywords)
                    if score >= 0.3:
                        matched_pages.append({"path": page, "confidence": score})
        else:
            # Keyword matching fallback
            matched_pages = []
            epic_keywords = epic_title.lower().split()
            for page in all_pages:
                page_lower = page.lower()
                score = sum(1 for word in epic_keywords if word in page_lower) / len(epic_keywords)
                if score >= 0.3:
                    matched_pages.append({"path": page, "confidence": score})
        
        if not matched_pages:
            return {"error": "No related wiki pages found for this Epic", "story_count": 0}
        
        print(f"📚 Found {len(matched_pages)} related wiki pages")
        
    except Exception as e:
        print(f"❌ Wiki search failed: {str(e)}")
        return {"error": f"Wiki search failed: {str(e)}"}
    
    # Step 3: Generate stories using existing function
    try:
        gen_request = GenerateRequest(
            wiki_page_paths=[p["path"] for p in matched_pages],
            epic_id=epic_id
        )
        result = await generate_stories(gen_request)
        
        story_count = len([s for s in result["stories"] if s["status"] == "created"])
        
        print(f"✅ Successfully created {story_count} stories")
        
        return {
            "success": True,
            "story_count": story_count,
            "message": f"Created {story_count} user stories",
            "wiki_pages_used": len(matched_pages)
        }
        
    except Exception as e:
        print(f"❌ Story generation failed: {str(e)}")
        return {"error": f"Story generation failed: {str(e)}"}


@app.post("/find_wiki_pages")
async def find_wiki_pages(request: FindWikiRequest):
    print(f"🔍 Finding wiki pages for: {request.epic_title}")
    
    try:
        all_pages = await get_all_wiki_pages()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch wiki pages: {str(e)}")
    
    if not all_pages:
        return {"pages": []}
    
    prompt = f"""You are analyzing which wiki pages are relevant to an Epic.

Epic Title: "{request.epic_title}"

Available Wiki Pages:
{chr(10).join(f"- {page}" for page in all_pages)}

Task: Identify which wiki pages are related to this epic and rate each match from 0.0 to 1.0.

Only include pages with confidence >= 0.6.

Response format (JSON):
{{
  "matches": [
    {{"path": "page-name", "confidence": 0.95, "reason": "why it matches"}}
  ]
}}

Response:"""

    try:
        response_text = call_openai([
            {"role": "system", "content": "You are an expert at matching documentation to project epics."},
            {"role": "user", "content": prompt}
        ], temperature=0.3)
        
        result = json.loads(response_text)
        pages = result.get("matches", [])
        
        print(f"✅ Found {len(pages)} related wiki pages")
        return {"pages": pages}
        
    except Exception as e:
        print(f"❌ AI matching failed: {str(e)}")
        matched = []
        epic_keywords = request.epic_title.lower().split()
        for page in all_pages:
            page_lower = page.lower()
            score = sum(1 for word in epic_keywords if word in page_lower) / len(epic_keywords)
            if score >= 0.3:
                matched.append({"path": page, "confidence": score, "reason": "Keyword match"})
        
        return {"pages": sorted(matched, key=lambda x: x["confidence"], reverse=True)}


async def get_all_wiki_pages():
    azure_org = os.getenv("AZURE_ORG_URL")
    azure_project = os.getenv("AZURE_PROJECT")
    azure_token = os.getenv("AZURE_TOKEN")
    
    auth_string = f":{azure_token}"
    basic_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/json"
    }
    
    wiki_url = f"{azure_org}/{azure_project}/_apis/wiki/wikis?api-version=7.0"
    res = requests.get(wiki_url, headers=headers)
    
    if res.status_code != 200:
        raise Exception(f"Failed to get wikis: {res.status_code}")
    
    wikis = res.json().get("value", [])
    if not wikis:
        return []
    
    wiki_id = wikis[0]["id"]
    
    pages_url = f"{azure_org}/{azure_project}/_apis/wiki/wikis/{wiki_id}/pages?recursionLevel=full&api-version=7.0"
    res = requests.get(pages_url, headers=headers)
    
    if res.status_code != 200:
        raise Exception(f"Failed to get pages: {res.status_code}")
    
    def extract_paths(page_data):
        paths = []
        if "path" in page_data:
            paths.append(page_data["path"].strip("/"))
        if "subPages" in page_data:
            for subpage in page_data["subPages"]:
                paths.extend(extract_paths(subpage))
        return paths
    
    data = res.json()
    all_paths = extract_paths(data)
    
    print(f"📚 Found {len(all_paths)} wiki pages total")
    return all_paths


@app.post("/generate_stories")
async def generate_stories(request: GenerateRequest):
    print(f"📖 Generating stories from {len(request.wiki_page_paths)} wiki pages")
    
    # MCP server runs on port 5001
    mcp_port = int(os.getenv("MCP_PORT", "5001"))
    
    print(f"🔗 Calling MCP server at localhost:{mcp_port}")
    
    # Fetch wiki content
    try:
        wiki_response = requests.post(
            f"http://127.0.0.1:{mcp_port}/tools/fetch_wiki/run",
            json={"args": {"page_paths": request.wiki_page_paths}},
            timeout=30
        )
        
        if wiki_response.status_code != 200:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to fetch wiki pages: {wiki_response.status_code}"
            )
        
        wiki_content = wiki_response.json().get("result", "")
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Could not connect to MCP server: {str(e)}"
        )
    
    if "Error" in wiki_content or not wiki_content:
        raise HTTPException(status_code=404, detail=f"Wiki pages not found")
    
    print(f"✅ Fetched wiki content ({len(wiki_content)} chars)")
    print("🤖 Generating user stories with LLM...")
    
    prompt = f"""Create 3-5 user stories per wiki page.

Format each story EXACTLY like this:
---STORY---
TITLE: User Story: [Brief title]
DESCRIPTION:
As a [user], I want [feature], so that [benefit].

Acceptance Criteria:
- [Criterion 1]
- [Criterion 2]
---END---

Wiki Content:
{wiki_content}

Generate stories now:"""

    try:
        llm_output = call_openai([
            {"role": "system", "content": "You are a product manager who writes clear user stories."},
            {"role": "user", "content": prompt}
        ], temperature=0.7)
        
        print(f"✅ LLM generated response")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
    
    stories = parse_stories(llm_output)
    print(f"📋 Parsed {len(stories)} user stories")
    
    created_stories = []
    
    for idx, story in enumerate(stories, 1):
        print(f"📤 Creating story {idx}/{len(stories)}")
        
        try:
            azure_response = requests.post(
                f"http://127.0.0.1:{mcp_port}/tools/create_story/run",
                json={
                    "args": {
                        "title": story["title"],
                        "description": story["description"],
                        "epic_id": request.epic_id
                    }
                },
                timeout=30
            )
            
            if azure_response.status_code == 200:
                result = azure_response.json().get("result", "")
                created_stories.append({
                    "title": story["title"],
                    "status": "created",
                    "result": result
                })
                print(f"✅ Story {idx} created")
            else:
                created_stories.append({
                    "title": story["title"],
                    "status": "failed",
                    "error": azure_response.text
                })
                print(f"❌ Story {idx} failed")
                
        except requests.exceptions.RequestException as e:
            created_stories.append({
                "title": story["title"],
                "status": "failed",
                "error": str(e)
            })
            print(f"❌ Story {idx} error: {str(e)}")
    
    return {
        "message": f"Generated {len(stories)} stories",
        "stories": created_stories
    }


def parse_stories(llm_output: str) -> list:
    stories = []
    story_blocks = llm_output.split("---STORY---")
    
    for block in story_blocks:
        if "---END---" not in block:
            continue
            
        content = block.split("---END---")[0].strip()
        lines = content.split("\n")
        title = ""
        description_lines = []
        in_description = False
        
        for line in lines:
            line = line.strip()
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "").strip()
            elif line.startswith("DESCRIPTION:"):
                in_description = True
            elif in_description and line:
                description_lines.append(line)
        
        if title and description_lines:
            description = "\n".join(description_lines)
            stories.append({"title": title, "description": description})
    
    return stories


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "Story Generator API"
    }

@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "Story Generator API"
    }