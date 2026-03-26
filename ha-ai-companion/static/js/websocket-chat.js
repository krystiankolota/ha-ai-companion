// WebSocket-based chat implementation to avoid Ingress buffering

let ws = null;
let currentAssistantMessage = null;
let currentMessageContent = '';
let loadingIndicator = null;
let toolCallArguments = {}; // Store arguments from tool_start events, keyed by tool_call_id

function connectWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        return ws;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Use relative path to work with Home Assistant Ingress proxy
    const wsUrl = `${protocol}//${window.location.host}${window.location.pathname}ws/chat`;

    console.log('Connecting to WebSocket:', wsUrl);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        addSystemMessage('❌ WebSocket connection error');
    };

    ws.onclose = () => {
        console.log('WebSocket closed');
        ws = null;
        // If we were mid-response, clean up so the UI doesn't freeze
        if (loadingIndicator && loadingIndicator.parentNode) {
            removeLoadingIndicator(loadingIndicator);
            loadingIndicator = null;
            addSystemMessage('⚠️ Connection lost. Your message may not have been processed — please try again.');
        }
        if (sendBtn && sendBtn.disabled) {
            sendBtn.disabled = false;
            if (messageInput) messageInput.focus();
        }
        // Save whatever we have so far
        if (typeof window.autoSaveSession === 'function') {
            window.autoSaveSession();
        }
    };

    return ws;
}

// Override the sendMessage function to use WebSocket
async function sendMessageWebSocket() {
    const message = messageInput.value.trim();
    if (!message) return;

    console.log('Sending message via WebSocket:', message);

    // Add user message to chat
    addUserMessage(message);

    // Add user message to conversation history
    conversationHistory.push({
        role: 'user',
        content: message
    });

    // Clear input
    messageInput.value = '';
    messageInput.style.height = 'auto';

    // Disable send button and show loading indicator
    sendBtn.disabled = true;
    currentAssistantMessage = null;
    currentMessageContent = '';
    loadingIndicator = addLoadingIndicator();
    toolCallArguments = {}; // Reset tool arguments for new conversation

    try {
        const ws = connectWebSocket();

        // Wait for connection
        if (ws.readyState !== WebSocket.OPEN) {
            await new Promise((resolve, reject) => {
                ws.onopen = resolve;
                ws.onerror = reject;
                setTimeout(() => reject(new Error('Connection timeout')), 5000);
            });
        }

        // Set up message handler for this request
        ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            handleWebSocketMessage(message);
        };

        // Send the chat request
        ws.send(JSON.stringify({
            type: 'chat',
            message: message,
            conversation_history: conversationHistory.slice(0, -1)
        }));

    } catch (error) {
        console.error('WebSocket send error:', error);
        removeLoadingIndicator(loadingIndicator);
        addSystemMessage(`❌ Error: ${error.message}`);
        sendBtn.disabled = false;
        messageInput.focus();
    }
}

function handleWebSocketMessage(message) {
    const eventType = message.event;
    const data = message.data;

    console.log('WebSocket event:', eventType);

    try {
        if (eventType === 'token') {
            // Remove loading indicator on first token
            if (loadingIndicator && loadingIndicator.parentNode) {
                removeLoadingIndicator(loadingIndicator);
                loadingIndicator = null;
            }

            // Accumulate content
            currentMessageContent += data.content;

            // Update or create assistant message element
            if (!currentAssistantMessage) {
                currentAssistantMessage = addAssistantMessageStreaming('');
            }
            updateAssistantMessageStreaming(currentAssistantMessage, currentMessageContent);

        } else if (eventType === 'message_complete') {
            // Add message to conversation history
            conversationHistory.push(data.message);

            // Finalize the assistant message display
            if (currentAssistantMessage) {
                finalizeAssistantMessageStreaming(currentAssistantMessage);
            }
            currentMessageContent = '';
            currentAssistantMessage = null;

            // Update token counter if usage data is available
            if (data.usage) {
                updateTokenCounter(
                    data.usage.input_tokens || 0,
                    data.usage.output_tokens || 0,
                    data.usage.cached_tokens || 0
                );
            }

        } else if (eventType === 'tool_call') {
            // Finalize current message if any
            if (currentAssistantMessage) {
                finalizeAssistantMessageStreaming(currentAssistantMessage);
                currentAssistantMessage = null;
            }

            // Add assistant message with tool calls to history
            conversationHistory.push({
                role: 'assistant',
                content: currentMessageContent,
                tool_calls: data.tool_calls
            });
            currentMessageContent = '';

            // Show tool execution indicator summary
            const toolNames = data.tool_calls.map(tc => tc.function.name).join(', ');
            addSystemMessage(`🔧 Calling ${data.tool_calls.length} tool(s): ${toolNames}`);

            // Re-add loading indicator while tools execute and AI processes next response
            if (!loadingIndicator) {
                loadingIndicator = addLoadingIndicator();
            }
            if (typeof updateLoadingStatus === 'function') {
                updateLoadingStatus(`Running: ${toolNames}…`);
            }

        } else if (eventType === 'tool_start') {
            // Store the arguments for later use when we get the tool_result
            if (data.tool_call_id && data.arguments) {
                toolCallArguments[data.tool_call_id] = data.arguments;
            }

            // Update loading indicator status text
            if (typeof updateLoadingStatus === 'function') {
                updateLoadingStatus(`Running: ${data.function}…`);
            }

            // Show individual tool execution start
            addSystemMessage(`▶️ Executing: ${data.function}...`);

        } else if (eventType === 'tool_result') {
            console.log('Tool result received:', data.function, 'success:', data.result?.success);

            // Add tool result to history
            conversationHistory.push({
                role: 'tool',
                tool_call_id: data.tool_call_id,
                content: JSON.stringify(data.result)
            });

            // Display tool result visually
            addToolResultMessage(data.function, data.result);

            // Process tool results (especially for propose_config_changes)
            if (data.function === 'propose_config_changes' && data.result.success) {
                const changesetData = {
                    changeset_id: data.result.changeset_id,
                    total_files: data.result.total_files,
                    files: data.result.files,
                    reason: data.result.reason
                };

                // Prefer arguments echoed back in tool_result, fall back to tool_start store
                const args = data.arguments || toolCallArguments[data.tool_call_id];
                if (args && args.changes) {
                    changesetData.file_changes_detail = args.changes;
                    changesetData.original_contents = extractOriginalContents(conversationHistory, args.changes);
                } else {
                    console.warn('propose_config_changes: no arguments found, approval card will have limited info');
                }

                addApprovalCard(changesetData);
            }

            // Incrementally save after each tool result so progress survives a refresh
            if (typeof window.autoSaveSession === 'function') {
                window.autoSaveSession();
            }

        } else if (eventType === 'complete') {
            console.log('Stream complete:', data);

            if (typeof updateLoadingStatus === 'function' && data.iterations > 1) {
                updateLoadingStatus(`Done (${data.iterations} iterations)`);
            }

            // Update token counter with final totals
            if (data.usage) {
                updateTokenCounter(
                    data.usage.input_tokens || 0,
                    data.usage.output_tokens || 0,
                    data.usage.cached_tokens || 0
                );
                if (data.usage.cost_usd !== undefined && typeof window.updateCostDisplay === 'function') {
                    window.updateCostDisplay(data.usage.cost_usd);
                }
            }

            // Final cleanup
            if (loadingIndicator && loadingIndicator.parentNode) {
                removeLoadingIndicator(loadingIndicator);
                loadingIndicator = null;
            }
            sendBtn.disabled = false;
            messageInput.focus();

            // Auto-save conversation
            if (typeof window.autoSaveSession === 'function') {
                window.autoSaveSession();
            }

        } else if (eventType === 'error') {
            addSystemMessage(`❌ Error: ${data.error}`);

            // Cleanup
            if (loadingIndicator && loadingIndicator.parentNode) {
                removeLoadingIndicator(loadingIndicator);
                loadingIndicator = null;
            }
            sendBtn.disabled = false;
            messageInput.focus();
        }

    } catch (e) {
        console.error('Error handling WebSocket message:', e);
    }
}

// Export for use in main app
window.sendMessageWebSocket = sendMessageWebSocket;
