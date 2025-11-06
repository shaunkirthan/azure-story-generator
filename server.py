from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

class FindWikiRequest(BaseModel):
    epic_title: str

class GenerateRequest(BaseModel):
    wiki_page_paths: list[str]
    epic_id: int = None

@app.post("/find_wiki_pages")
async def find_wiki_pages(request: FindWikiRequest):
    """
    Uses AI to find wiki pages related to the epic title
    Returns pages with confidence scores
    """
    print(f"🔍 Finding wiki pages for: {request.epic_title}")
    
    # Get all wiki pages from Azure
    try:
        all_pages = await get_all_wiki_pages()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch wiki pages: {str(e)}")
    
    if not all_pages:
        return {"pages": []}
    
    # Use AI to match pages to epic
    prompt = f"""You are analyzing which wiki pages are relevant to an Epic.

Epic Title: "{request.epic_title}"

Available Wiki Pages:
{chr(10).join(f"- {page}" for page in all_pages)}

Task: Identify which wiki pages are related to this epic and rate each match from 0.0 to 1.0 (0 = not related, 1.0 = highly related).

Only include pages with confidence >= 0.6.

Response format (JSON):
{{
  "matches": [
    {{"path": "page-name", "confidence": 0.95, "reason": "why it matches"}},
    {{"path": "another-page", "confidence": 0.85, "reason": "why it matches"}}
  ]
}}

Response:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert at matching documentation to project epics."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )
        
        import json
        result = json.loads(response.choices[0].message.content)
        pages = result.get("matches", [])
        
        print(f"✅ Found {len(pages)} related wiki pages")
        return {"pages": pages}
        
    except Exception as e:
        print(f"❌ AI matching failed: {str(e)}")
        # Fallback: simple keyword matching
        matched = []
        epic_keywords = request.epic_title.lower().split()
        for page in all_pages:
            page_lower = page.lower()
            score = sum(1 for word in epic_keywords if word in page_lower) / len(epic_keywords)
            if score >= 0.3:
                matched.append({"path": page, "confidence": score, "reason": "Keyword match"})
        
        return {"pages": sorted(matched, key=lambda x: x["confidence"], reverse=True)}


async def get_all_wiki_pages():
    """
    Fetches list of all wiki pages from Azure DevOps
    """
    azure_org = os.getenv("AZURE_ORG_URL")
    azure_project = os.getenv("AZURE_PROJECT")
    azure_token = os.getenv("AZURE_TOKEN")
    
    import base64
    auth_string = f":{azure_token}"
    basic_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/json"
    }
    
    # Get wiki ID
    wiki_url = f"{azure_org}/{azure_project}/_apis/wiki/wikis?api-version=7.0"
    res = requests.get(wiki_url, headers=headers)
    
    if res.status_code != 200:
        raise Exception(f"Failed to get wikis: {res.status_code}")
    
    wikis = res.json().get("value", [])
    if not wikis:
        return []
    
    wiki_id = wikis[0]["id"]
    
    # Get all pages
    pages_url = f"{azure_org}/{azure_project}/_apis/wiki/wikis/{wiki_id}/pages?recursionLevel=full&api-version=7.0"
    res = requests.get(pages_url, headers=headers)
    
    if res.status_code != 200:
        raise Exception(f"Failed to get pages: {res.status_code}")
    
    # Extract page paths
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
    """
    Generates stories from wiki pages and links them to epic
    """
    print(f"📖 Generating stories from {len(request.wiki_page_paths)} wiki pages")
    
    # Fetch wiki content
    wiki_response = requests.post(
        "http://127.0.0.1:5001/tools/fetch_wiki/run",
        json={"args": {"page_paths": request.wiki_page_paths}}
    )
    
    if wiki_response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch wiki pages")
    
    wiki_content = wiki_response.json().get("result", "")
    
    if "Error" in wiki_content:
        raise HTTPException(status_code=404, detail=f"Wiki pages not found: {wiki_content}")
    
    print(f"✅ Fetched wiki content ({len(wiki_content)} chars)")
    
    # Generate stories with LLM
    print("🤖 Generating user stories with LLM...")
    
    prompt = f"""You are a product manager creating user stories for Azure DevOps.

Based on the following feature descriptions from Azure Wiki, create 3-5 user stories per wiki page.

IMPORTANT FORMATTING RULES:
1. Each user story must have:
   - A clear, concise title (format: "User Story: [Brief Description]")
   - A description with TWO parts:
     a) User story statement: "As a [user type], I want [feature], so that [benefit]"
     b) Acceptance criteria as a bulleted list

2. Format each story EXACTLY like this:
---STORY---
TITLE: User Story: [Brief title without markdown symbols]
DESCRIPTION:
As a [user], I want [feature], so that [benefit].

Acceptance Criteria:
- [Criterion 1]
- [Criterion 2]
- [Criterion 3]
---END---

DO NOT use markdown symbols like ###, **, or #.
DO NOT create separate items for acceptance criteria.
Each story should be ONE complete unit with title and description together.

Wiki Content:
{wiki_content}

Generate the user stories now:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a product manager who writes clear, actionable user stories for agile development."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        
        llm_output = response.choices[0].message.content
        print(f"✅ LLM generated response ({len(llm_output)} chars)")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
    
    # Parse stories
    stories = parse_stories(llm_output)
    print(f"📋 Parsed {len(stories)} user stories")
    
    # Create stories in Azure
    created_stories = []
    
    for idx, story in enumerate(stories, 1):
        print(f"📤 Creating story {idx}/{len(stories)}: {story['title'][:50]}...")
        
        azure_response = requests.post(
            "http://127.0.0.1:5001/tools/create_story/run",
            json={
                "args": {
                    "title": story["title"],
                    "description": story["description"],
                    "epic_id": request.epic_id
                }
            }
        )
        
        if azure_response.status_code == 200:
            result = azure_response.json().get("result", "")
            created_stories.append({
                "title": story["title"],
                "status": "created",
                "result": result
            })
            print(f"✅ Story {idx} created successfully")
        else:
            created_stories.append({
                "title": story["title"],
                "status": "failed",
                "error": azure_response.text
            })
            print(f"❌ Story {idx} failed")
    
    return {
        "message": f"Generated {len(stories)} user stories from {len(request.wiki_page_paths)} wiki pages",
        "stories": created_stories
    }


def parse_stories(llm_output: str) -> list:
    """Parse LLM output into structured stories"""
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
    
    # Fallback parsing
    if not stories:
        print("⚠️ Structured parsing failed, trying fallback...")
        parts = llm_output.split("User Story")
        for part in parts[1:]:
            lines = part.strip().split("\n")
            if len(lines) >= 3:
                title = "User Story" + lines[0].split(":")[0] + ": " + ":".join(lines[0].split(":")[1:]).strip()
                description = "\n".join(lines[1:]).strip()
                if description:
                    stories.append({"title": title, "description": description})
    
    return stories


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Story Generator (Azure Wiki + Extension)"}