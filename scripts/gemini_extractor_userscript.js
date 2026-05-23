// ==UserScript==
// @name         Gemini to Claude Command Center (Antigravity)
// @namespace    http://tampermonkey.net/
// @version      1.2
// @description  Extracts Gemini conversations and sends them to local CCC for Antigravity continuation.
// @author       You
// @match        https://gemini.google.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// ==/UserScript==

(function() {
    'use strict';

    async function extractAndSend() {
        const btn = document.getElementById('ccc-export-btn');
        if (btn) {
            btn.innerText = 'Scrolling...';
            btn.disabled = true;
        }

        // Auto-scroll to the top to load all messages
        // Gemini uses various scroll containers depending on the route/version
        const scrollContainers = [
            document.querySelector('infinite-scroller'),
            document.querySelector('cdk-virtual-scroll-viewport'),
            document.querySelector('#chat-history'),
            document.querySelector('main'),
            window
        ];
        const scrollContainer = scrollContainers.find(c => c !== null && (c === window || c.scrollHeight > c.clientHeight));
        
        let lastHeight = 0;
        let retries = 0;
        
        while (retries < 6) {
            if (scrollContainer === window) {
                window.scrollTo(0, 0);
            } else {
                scrollContainer.scrollTop = 0;
            }
            
            // Wait longer to ensure network payloads complete
            await new Promise(r => setTimeout(r, 1500));
            
            const currentHeight = scrollContainer === window ? document.body.scrollHeight : scrollContainer.scrollHeight;
            if (currentHeight === lastHeight) {
                retries++;
            } else {
                retries = 0;
                lastHeight = currentHeight;
            }
        }

        if (btn) btn.innerText = 'Extracting...';
        
        const messages = [];
        const chatContainer = document.querySelector('infinite-scroller, #chat-history, main') || document.body;
        
        // Grab user-query tags (which hold user prompts) and message-content tags (which hold model responses)
        const rawElements = chatContainer.querySelectorAll('user-query, message-content');
        
        if (rawElements.length > 0) {
            rawElements.forEach(el => {
                const tagName = el.tagName.toLowerCase();
                
                // If it's a message-content inside a user-query, skip it so we don't double-count the text
                if (tagName === 'message-content' && el.closest('user-query')) {
                    return;
                }
                
                // By elimination, if it's a user-query it's the user. Otherwise, it's the model!
                const isUser = tagName === 'user-query';
                const role = isUser ? 'USER_INPUT' : 'TEXT_RESPONSE';
                
                let text = el.innerText || el.textContent;
                
                if (isUser && text.startsWith('You\n')) {
                    text = text.substring(4);
                }
                
                if (text && text.trim()) {
                    messages.push({ role: role, content: text.trim() });
                }
            });
        }

        // Deduplicate messages in case the UI renders the same block twice
        const uniqueMessages = [];
        const seen = new Set();
        for (const msg of messages) {
            if (!seen.has(msg.content) && msg.content.length > 0) {
                seen.add(msg.content);
                uniqueMessages.push(msg);
            }
        }

        if (uniqueMessages.length === 0) {
            alert('Could not find any messages in the DOM. Gemini UI might have changed.');
            if (btn) {
                btn.innerText = 'Failed';
                btn.disabled = false;
            }
            return;
        }

        if (btn) btn.innerText = 'Sending...';
        
        const payload = {
            title: document.title || 'Imported Gemini Chat',
            messages: uniqueMessages
        };

        GM_xmlhttpRequest({
            method: 'POST',
            url: 'http://127.0.0.1:8090/api/ingest/gemini',
            headers: {
                'Content-Type': 'application/json'
            },
            data: JSON.stringify(payload),
            anonymous: true,
            onload: function(response) {
                if (response.status >= 200 && response.status < 300) {
                    try {
                        const data = JSON.parse(response.responseText);
                        if (btn) {
                            btn.innerText = 'Sent! ' + data.session_id.substring(0, 8);
                            setTimeout(() => { btn.innerText = 'Send to CCC'; btn.disabled = false; }, 3000);
                        } else {
                            alert('Successfully sent to CCC! Session ID: ' + data.session_id.substring(0, 8));
                        }
                    } catch (e) {
                        alert('Success, but failed to parse response.');
                        if (btn) {
                            btn.innerText = 'Send to CCC';
                            btn.disabled = false;
                        }
                    }
                } else if (response.status === 403) {
                     alert('CCC rejected the request (CORS/Origin issue). Please add https://gemini.google.com to ALLOWED_ORIGINS in CCC network settings.');
                     if (btn) {
                         btn.innerText = 'Failed (403)';
                         btn.disabled = false;
                     }
                } else {
                    alert(`HTTP error! status: ${response.status}`);
                    if (btn) {
                        btn.innerText = 'Failed';
                        btn.disabled = false;
                    }
                }
            },
            onerror: function(err) {
                console.error(err);
                alert('Failed to send to local server. Is CCC running on port 8090?');
                if (btn) {
                    btn.innerText = 'Send to CCC';
                    btn.disabled = false;
                }
            }
        });
    }

    GM_registerMenuCommand("Extract Chat to CCC", extractAndSend);

    function injectButton() {
        if (document.getElementById('ccc-export-btn')) return;
        
        const btn = document.createElement('button');
        btn.id = 'ccc-export-btn';
        btn.innerText = 'Send to CCC';
        btn.style.position = 'fixed';
        btn.style.bottom = '20px';
        btn.style.right = '20px';
        btn.style.zIndex = '2147483647';
        btn.style.padding = '10px 15px';
        btn.style.backgroundColor = '#D4A373';
        btn.style.color = '#fff';
        btn.style.border = 'none';
        btn.style.borderRadius = '5px';
        btn.style.cursor = 'pointer';
        btn.style.boxShadow = '0 2px 5px rgba(0,0,0,0.3)';
        btn.style.fontFamily = 'sans-serif';
        btn.style.fontSize = '14px';

        btn.addEventListener('click', extractAndSend);
        document.body.appendChild(btn);
    }

    injectButton();

    const observer = new MutationObserver(() => {
        if (!document.getElementById('ccc-export-btn')) {
            injectButton();
        }
    });
    
    observer.observe(document.body, { childList: true, subtree: true });

})();
