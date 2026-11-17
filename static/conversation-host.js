/* The standalone host composes the dashboard's conversation controllers. */
(function (root) {
  'use strict';
  function createHost({ documentReady } = {}) {
    const state = {};
    const adapters = { state, helpers: state, documentReady };
    for (const controller of [root.CCCConversationCore, root.CCCConversationPanes,
      root.CCCConversationControls, root.CCCConversationInteractions,
      root.CCCConversationServices, root.CCCConversationViews,
      root.CCCConversationChrome, root.CCCConversationPreferences,
      root.CCCConversationInput, root.CCCConversationBootstrap]) {
      Object.assign(state, controller.createController(adapters));
    }
    const text = root.CCCConversationText.createRenderer({
      cleanIssuePrompt: (...args) => state.cleanIssuePrompt(...args),
      sidebarRowDisplayTitle: (...args) => state.sidebarRowDisplayTitle(...args),
      shortSessionId: (...args) => state.shortSessionId(...args),
      sessionCompletionDetails: (...args) => state.sessionCompletionDetails(...args),
      activeSessionId: () => state.sessionIdByConv[state.currentConversation]
        || state.currentSession?.id || '',
      findSession: id => state.conversationsData.find(row => String(row.session_id || row.id || '') === id)
        || state.archiveData.find(row => String(row.session_id || row.id || '') === id),
    });
    Object.assign(state, text, root.CCCConversationContent.createHelpers(text),
      root.CCCConversationAnnotations.createController(adapters));

    // Navigation panels belong to the surrounding dashboard. Conversation
    // actions keep their state and goal chrome current and publish changes
    // for hosts without instantiating hidden sidebar/queue render trees.
    function navigationChanged(view) {
      document.dispatchEvent(new CustomEvent('ccc:conversation-navigation-changed', {
        detail: { view, conversationId: state.currentConversation },
      }));
    }
    Object.assign(state, {
      renderSidebar() {
        state.renderConversationGoalStrip(state.activePaneId(), state.currentConversationRow());
        navigationChanged('sessions');
      },
      renderFlowSidebar() { navigationChanged('flow'); },
      renderArchiveList() { navigationChanged('archive'); },
      _renderQueuePanel() { navigationChanged('queues'); },
      async refreshConversationList() {
        await state.refreshArchiveData({ force: true });
        if (state.popoutRepoPath()) await state.loadConversationList();
        state.renderSidebar();
      },
      openSettingsModal() {
        window.open('/?ccc_settings=appearance', '_blank', 'noopener');
      },
    });
    let detailsPromise;
    async function ensureDetails() {
      if (root.CCCConversationDetails) {
        Object.assign(state, root.CCCConversationDetails.createController({
          ...adapters, functions: state,
        }));
        return;
      }
      if (!detailsPromise) {
        const dependency = document.querySelector('[data-ccc-conversation-details]');
        detailsPromise = new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = dependency?.getAttribute('src') || '/static/conversation-details.js';
          script.onload = () => root.CCCConversationDetails
            ? resolve() : reject(new Error('Conversation details did not initialize'));
          script.onerror = () => { script.remove(); reject(new Error('Conversation details could not load')); };
          document.head.appendChild(script);
        }).catch(error => { detailsPromise = null; throw error; });
      }
      await detailsPromise;
      Object.assign(state, root.CCCConversationDetails.createController({
        ...adapters, functions: state,
      }));
    }
    for (const name of ['loadConversationList', '_uxqOpenItemDetail']) {
      state[name] = async (...args) => {
        try {
          await ensureDetails();
          return state[name](...args);
        } catch (error) {
          state.showOpToast('Could not load conversation details. Try again.', 'error');
          return null;
        }
      };
    }
    let issuesPromise;
    async function ensureIssues() {
      if (!root.CCCConversationIssues && !issuesPromise) {
        const dependency = document.querySelector('[data-ccc-conversation-issues]');
        issuesPromise = new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = dependency?.getAttribute('src') || '/static/conversation-issues.js';
          script.onload = () => root.CCCConversationIssues
            ? resolve() : reject(new Error('Conversation issues did not initialize'));
          script.onerror = () => { script.remove(); reject(new Error('Conversation issues could not load')); };
          document.head.appendChild(script);
        }).catch(error => { issuesPromise = null; throw error; });
      }
      if (issuesPromise) await issuesPromise;
      Object.assign(state, root.CCCConversationIssues.createController(adapters));
    }
    for (const name of ['spawnFromBacklogIssue', 'closeIssueFromInputBar',
      'replyToIssueFromInputBar', 'renderIssueInConvPane', '_renderBacklogDetail',
      '_renderGithubPrDetail']) {
      state[name] = async (...args) => {
        try {
          await ensureIssues();
          return state[name](...args);
        } catch (error) {
          state.showOpToast('Could not load issue controls. Try again.', 'error');
          return name.startsWith('_render') ? '' : null;
        }
      };
    }
    const initializers = root.CCCConversationDefaults.createInitializers(adapters);
    for (const [name, initialize] of Object.entries(initializers)) state[name] = initialize();
    for (const name of ['currentConversation', 'convLastLine', 'convEventSource',
      '_firstUserMsgRendered', '_pendingSends', '_currentToolGroup',
      '_currentToolCount', 'currentSession']) {
      root.CCCConversationState.bindLegacyProperty(state, state.splitState, name);
      root.CCCConversationState.bindLegacyProperty(root, state.splitState, name);
    }
    state.activeTab = 'sessions';
    state.kanbanView = false;
    Object.assign(state, root.CCCConversationTransport.createController(adapters));
    Object.assign(state, root.CCCConversationEvents.createRenderer({ ...adapters, text }));
    Object.assign(state, root.CCCConversationComposer.createComposer(adapters));
    Object.assign(state, root.CCCConversationSelection.createController({
      state, panes: state, transport: state, composer: state, navigation: state,
      chrome: state, replay: state, pkood: state,
    }));
    return state;
  }
  root.CCCConversationHost = Object.freeze({ createHost });
})(typeof window === 'undefined' ? globalThis : window);
