(async function () {
  console.log("Extension script starting...");
  
  const statusEl = document.getElementById("status");
  const btn = document.getElementById("go");
  
  // URL of your backend (e.g., Ngrok or Render URL)
  const BACKEND_URL = "https://azure-story-generator.onrender.com"; // Change to your backend URL
  
  function updateStatus(message, type = 'info') {
    statusEl.textContent = message;
    statusEl.className = type;
    console.log(`[${type}]`, message);
  }

  try {
    updateStatus("Initializing...", "loading");
    
    // Wait for SDK initialization
    await SDK.init();
    await SDK.ready();
    SDK.notifyLoadSucceeded();
    
    updateStatus("Ready to generate stories", "success");
    console.log("SDK initialized successfully");

    const workItemFormSvc = await SDK.getService("ms.vss-work-web.work-item-form");

    btn.addEventListener("click", async () => {
      try {
        btn.disabled = true;
        updateStatus("Reading Epic...", "loading");
        
        const epicId = await workItemFormSvc.getId();
        const epicTitle = await workItemFormSvc.getFieldValue("System.Title");

        console.log("Epic ID:", epicId, "Title:", epicTitle);

        if (!epicTitle) {
          throw new Error("Could not read Epic title");
        }

        // Step 1: Find related wiki pages
        updateStatus("🔍 Finding related wiki pages...", "loading");
        
        const findRes = await fetch(`${BACKEND_URL}/find_wiki_pages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ epic_title: epicTitle })
        });

        if (!findRes.ok) {
          throw new Error(`Failed to find wiki pages: ${findRes.status}`);
        }

        const wikiData = await findRes.json();
        console.log("Wiki pages found:", wikiData.pages.length);

        if (!wikiData.pages || wikiData.pages.length === 0) {
          updateStatus("⚠️ No related wiki pages found", "error");
          btn.disabled = false;
          return;
        }

        updateStatus(`Found ${wikiData.pages.length} wiki page(s)`, "success");

        // Step 2: Generate stories
        updateStatus(`📝 Generating stories...`, "loading");
        
        const genRes = await fetch(`${BACKEND_URL}/generate_stories`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            wiki_page_paths: wikiData.pages.map(p => p.path),
            epic_id: epicId
          })
        });

        if (!genRes.ok) {
          throw new Error(`Generation failed: ${genRes.status}`);
        }

        const result = await genRes.json();
        const successCount = result.stories.filter(s => s.status === 'created').length;
        
        updateStatus(`✅ Created ${successCount} user stories! Refreshing...`, "success");
        console.log("Result:", result);
        
        setTimeout(() => {
          workItemFormSvc.refresh();
        }, 2000);

      } catch (err) {
        console.error("Error:", err);
        updateStatus(`❌ Error: ${err.message}`, "error");
      } finally {
        btn.disabled = false;
      }
    });
    
  } catch (e) {
    console.error("Extension init failed:", e);
    updateStatus(`Failed to load: ${e.message}`, "error");
    SDK.notifyLoadFailed(e.message);
  }
})();
