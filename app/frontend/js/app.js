async function initializeApp() {
      initializeSidebar();
      updateThemeButton();
      initializeModelSettings();
      checkHealth();
      if (await initializeAuth()) {
        await Promise.all([loadProjects(), loadConversations()]);
      }
    }

    initializeApp();
