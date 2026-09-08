/**
 * Apps Interface — AI-Powered App Builder
 * Handles GitHub auth, theme selection, generation, and deployment
 */

class AppsInterface {
  constructor() {
    this.githubToken = localStorage.getItem('aiorch_github_token');
    this.currentTheme = localStorage.getItem('aiorch_theme') || 'modern-minimal';
    this.init();
  }

  init() {
    this.setupEventListeners();
    this.updateAuthStatus();
    this.displayThemeSelector();
  }

  setupEventListeners() {
    // GitHub login
    const githubLoginBtn = document.getElementById('githubLoginBtn');
    if (githubLoginBtn) {
      githubLoginBtn.addEventListener('click', () => this.initiateGitHubAuth());
    }

    // Theme selector
    document.querySelectorAll('[data-theme]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.selectTheme(e.target.dataset.theme);
      });
    });

    // Generate button
    const generateBtn = document.getElementById('generateAppBtn');
    if (generateBtn) {
      generateBtn.addEventListener('click', () => this.generateApp());
    }

    // Chat input
    const chatInput = document.getElementById('appChatInput');
    if (chatInput) {
      chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.generateApp();
        }
      });
    }
  }

  displayThemeSelector() {
    const themesHtml = `
      <div class="theme-grid">
        <button data-theme="modern-minimal" class="theme-btn ${this.currentTheme === 'modern-minimal' ? 'active' : ''}">
          <div class="theme-preview modern"></div>
          <span>Modern Minimal</span>
          <small>Clean & Professional</small>
        </button>
        <button data-theme="bold-colorful" class="theme-btn ${this.currentTheme === 'bold-colorful' ? 'active' : ''}">
          <div class="theme-preview bold"></div>
          <span>Bold & Colorful</span>
          <small>Vibrant & Startup</small>
        </button>
        <button data-theme="dark-premium" class="theme-btn ${this.currentTheme === 'dark-premium' ? 'active' : ''}">
          <div class="theme-preview premium"></div>
          <span>Dark Premium</span>
          <small>Luxury & Tech</small>
        </button>
      </div>
    `;

    const themeContainer = document.getElementById('themeSelector');
    if (themeContainer) {
      themeContainer.innerHTML = themesHtml;
      // Re-attach listeners
      document.querySelectorAll('[data-theme]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          this.selectTheme(e.currentTarget.dataset.theme);
        });
      });
    }
  }

  selectTheme(theme) {
    this.currentTheme = theme;
    localStorage.setItem('aiorch_theme', theme);
    this.displayThemeSelector();
  }

  initiateGitHubAuth() {
    const clientId = 'Ov23liAX99uZcGAYyzk9';
    const redirectUri = `${window.location.origin}/ai-orchestration/`;
    const scope = 'repo,user:email';

    const authUrl = `https://github.com/login/oauth/authorize?client_id=${clientId}&redirect_uri=${redirectUri}&scope=${scope}`;
    window.location.href = authUrl;
  }

  updateAuthStatus() {
    const authStatus = document.getElementById('authStatus');
    const chatArea = document.getElementById('appChatArea');

    if (this.githubToken) {
      if (authStatus) authStatus.innerHTML = `<div class="success-badge">✅ Connected to GitHub</div>`;
      if (chatArea) chatArea.classList.remove('hidden');
    } else {
      if (authStatus) authStatus.innerHTML = `<button id="githubLoginBtn" class="btn-primary">🔗 Connect GitHub</button>`;
      if (chatArea) chatArea.classList.add('hidden');
      this.setupEventListeners();
    }
  }

  async generateApp() {
    const prompt = document.getElementById('appChatInput')?.value.trim();
    if (!prompt) {
      alert('Please describe the app you want to build');
      return;
    }

    if (!this.githubToken) {
      alert('Please login to GitHub first');
      return;
    }

    const mode = document.getElementById('appMode')?.value || 'prototype';
    const generateBtn = document.getElementById('generateAppBtn');
    const responseArea = document.getElementById('appResponse');

    // Disable button and clear response
    if (generateBtn) generateBtn.disabled = true;
    if (responseArea) responseArea.innerHTML = '<div class="generating">⏳ Generating your app...</div>';

    try {
      const response = await fetch('https://ready4launch.onrender.com/api/apps/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          mode,
          theme: this.currentTheme,
          githubToken: this.githubToken,
        }),
      });

      if (!response.ok) throw new Error(`API error: ${response.statusText}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      let html = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value);
        const lines = text.split('\n').filter(l => l.trim());

        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          try {
            const event = JSON.parse(line.slice(5));

            if (event.type === 'status') {
              if (responseArea) {
                responseArea.innerHTML = `<div class="status-message">${event.message}</div>`;
              }
            } else if (event.type === 'chunk') {
              html += event.content || '';
            } else if (event.type === 'success') {
              this.displaySuccess(event);
            } else if (event.type === 'error') {
              this.displayError(event);
            }
          } catch (e) {
            // JSON parse error, skip
          }
        }
      }
    } catch (err) {
      this.displayError({ message: err.message });
    } finally {
      if (generateBtn) generateBtn.disabled = false;
    }
  }

  displaySuccess(data) {
    const responseArea = document.getElementById('appResponse');
    if (!responseArea) return;

    const html = `
      <div class="success-card">
        <div class="success-header">✅ App Generated Successfully!</div>

        <div class="result-section">
          <h4>Live Preview</h4>
          <a href="${data.liveUrl}" target="_blank" class="link-button">
            🌐 Open Live App → ${data.liveUrl}
          </a>
        </div>

        <div class="result-section">
          <h4>GitHub Repository</h4>
          <a href="${data.repoUrl}" target="_blank" class="link-button">
            💻 View Repository → ${data.repoUrl}
          </a>
        </div>

        <div class="result-section">
          <h4>Deployment Details</h4>
          <table class="details-table">
            <tr><td>Repository:</td><td><code>${data.repoName}</code></td></tr>
            <tr><td>Owner:</td><td><code>${data.owner}</code></td></tr>
            <tr><td>Files:</td><td>${data.files.length} files deployed</td></tr>
          </table>
        </div>

        <div class="action-buttons">
          <button class="btn-secondary" onclick="window.open('${data.liveUrl}', '_blank')">Preview App</button>
          <button class="btn-secondary" onclick="window.open('${data.repoUrl}', '_blank')">Edit on GitHub</button>
          <button class="btn-secondary" onclick="location.reload()">Build Another</button>
        </div>
      </div>
    `;

    responseArea.innerHTML = html;
  }

  displayError(data) {
    const responseArea = document.getElementById('appResponse');
    if (responseArea) {
      responseArea.innerHTML = `<div class="error-card">❌ Error: ${data.message}</div>`;
    }
  }

  handleGitHubCallback(code) {
    fetch('https://ready4launch.onrender.com/api/auth/github-token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.access_token) {
          localStorage.setItem('aiorch_github_token', data.access_token);
          this.githubToken = data.access_token;
          this.updateAuthStatus();
          // Clean up URL
          window.history.replaceState({}, document.title, '/ai-orchestration/');
        }
      })
      .catch(err => console.error('Auth error:', err));
  }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
  window.appsInterface = new AppsInterface();

  // Check for OAuth callback
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  if (code) {
    window.appsInterface.handleGitHubCallback(code);
  }
});
