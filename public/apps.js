/**
 * Apps Interface — AI-Powered App Builder & GitHub Deployer
 * Handles GitHub auth, repository loading, theme selection, generation, and deployment
 */

class AppsInterface {
  constructor() {
    this.githubToken = localStorage.getItem('aiorch_github_token') || localStorage.getItem('aios_github_token') || '';
    this.githubUser = localStorage.getItem('aiorch_github_user') || localStorage.getItem('aios_github_user') || (window.currentGitHubUser || 'karnkeshav');
    this.currentTheme = localStorage.getItem('aiorch_theme') || 'modern-minimal';
    this.cachedRepos = [];
    this.selectedRepo = null;
    this.init();
  }

  init() {
    this.setupEventListeners();
    this.updateAuthStatus();
    this.loadRepos();
  }

  setupEventListeners() {
    // GitHub login button
    const githubLoginBtn = document.getElementById('githubLoginBtn');
    if (githubLoginBtn) {
      githubLoginBtn.onclick = () => this.initiateGitHubAuth();
    }
  }

  syncWithGlobalAuth(username, token) {
    this.githubUser = username || 'karnkeshav';
    this.githubToken = token || '';
    if (token) {
      localStorage.setItem('aiorch_github_token', token);
      localStorage.setItem('aios_github_token', token);
    }
    if (username) {
      localStorage.setItem('aiorch_github_user', username);
      localStorage.setItem('aios_github_user', username);
    }
    this.updateAuthStatus();
    this.loadRepos(true);
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
    if (typeof window.openGitHubLoginModal === 'function') {
      window.openGitHubLoginModal();
    } else {
      const clientId = 'Ov23liAX99uZcGAYyzk9';
      const redirectUri = `${window.location.origin}/ai-orchestration/`;
      const scope = 'repo,user:email';
      const authUrl = `https://github.com/login/oauth/authorize?client_id=${clientId}&redirect_uri=${redirectUri}&scope=${scope}`;
      window.location.href = authUrl;
    }
  }

  updateAuthStatus() {
    const authStatus = document.getElementById('authStatus');
    const repoSection = document.getElementById('appsRepoSection');
    
    // Check if user is connected
    const isConnected = !!(this.githubToken || this.githubUser);
    const displayUser = this.githubUser || 'karnkeshav';
    const avatarUrl = `https://avatars.githubusercontent.com/${displayUser}`;

    if (isConnected) {
      if (authStatus) {
        authStatus.innerHTML = `
          <div class="apps-auth-card">
            <div class="apps-auth-info">
              <img src="${avatarUrl}" alt="${displayUser}" class="apps-auth-avatar" onerror="this.src='https://avatars.githubusercontent.com/u/160194487?v=4'">
              <div class="apps-auth-details">
                <div class="apps-auth-username">@${displayUser}</div>
                <div class="apps-auth-status">
                  <span class="apps-auth-status-dot"></span> GitHub Connected & Active
                </div>
              </div>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <button type="button" class="btn-secondary" onclick="if(window.appsInterface) window.appsInterface.loadRepos(true)" style="padding: 0.45rem 0.85rem; font-size: 0.8rem;">
                🔄 Refresh Repos
              </button>
              <button type="button" class="btn-secondary" onclick="if(typeof openGitHubLoginModal === 'function') openGitHubLoginModal()" style="padding: 0.45rem 0.85rem; font-size: 0.8rem;">
                🔑 Switch Account
              </button>
            </div>
          </div>
        `;
      }
      if (repoSection) repoSection.style.display = 'block';
    } else {
      if (authStatus) {
        authStatus.innerHTML = `<button id="githubLoginBtn" class="btn-primary" onclick="if(window.appsInterface) window.appsInterface.initiateGitHubAuth()">🔗 Connect GitHub Account</button>`;
      }
      if (repoSection) repoSection.style.display = 'none';
      this.setupEventListeners();
    }
  }

  async loadRepos(force = false) {
    const listContainer = document.getElementById('appsRepoList');
    if (!force && this.cachedRepos && this.cachedRepos.length > 0 && listContainer && listContainer.dataset.loaded === 'true') {
      return;
    }

    const targetUser = this.githubUser || localStorage.getItem('aios_github_user') || 'karnkeshav';
    const token = this.githubToken || localStorage.getItem('aios_github_token') || '';

    if (listContainer) {
      listContainer.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 1.5rem; font-size: 0.85rem; color: var(--text-muted);">
          <span class="console-spinner" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 6px;"></span> Loading your repositories...
        </div>
      `;
    }

    // 1. Try Backend API
    try {
      const headers = {};
      if (token) headers['x-github-token'] = token;
      if (targetUser) headers['x-github-user'] = targetUser;

      let apiFetchFunc = window.apiFetch;
      let resp;
      if (typeof apiFetchFunc === 'function') {
        resp = await apiFetchFunc(`/api/github-repos?user=${encodeURIComponent(targetUser)}&limit=30`, { headers });
      } else {
        resp = await fetch(`http://localhost:8000/api/github-repos?user=${encodeURIComponent(targetUser)}&limit=30`, { headers });
      }

      if (resp && resp.ok) {
        const data = await resp.json();
        if (data.repos && data.repos.length > 0) {
          this.cachedRepos = data.repos;
          this.renderAppRepos(data.repos);
          return;
        }
      }
    } catch (e) {
      console.warn('AppsInterface: Backend repo fetch failed, trying direct GitHub API fallback:', e);
    }

    // 2. Direct GitHub API Fallback
    try {
      let directUrl = `https://api.github.com/users/${encodeURIComponent(targetUser)}/repos?sort=updated&per_page=30`;
      const directHeaders = { 'Accept': 'application/vnd.github.v3+json' };
      if (token) {
        directHeaders['Authorization'] = `Bearer ${token}`;
        directUrl = `https://api.github.com/user/repos?sort=updated&per_page=30&affiliation=owner,collaborator`;
      }
      const ghRes = await fetch(directUrl, { headers: directHeaders });
      if (ghRes.ok) {
        const rawRepos = await ghRes.json();
        const repos = rawRepos.map(r => {
          const rOwner = (r.owner && r.owner.login) ? r.owner.login : targetUser;
          return {
            name: r.name,
            full_name: r.full_name || `${rOwner}/${r.name}`,
            description: r.description || '',
            url: r.html_url || `https://github.com/${rOwner}/${r.name}`,
            homepageUrl: r.homepage || '',
            pages_url: `https://${rOwner}.github.io/${r.name}/`,
            is_private: r.private || false,
            updatedAt: r.updated_at
          };
        });
        this.cachedRepos = repos;
        this.renderAppRepos(repos);
        return;
      }
    } catch (err) {
      console.error('AppsInterface: Direct GitHub API fallback failed:', err);
    }

    if (listContainer) {
      listContainer.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 1rem; font-size: 0.82rem; color: var(--accent-rose);">
          Could not load repositories. Please verify your connection or GitHub token.
        </div>
      `;
    }
  }

  filterRepos(query) {
    if (!this.cachedRepos) return;
    const q = (query || '').toLowerCase().trim();
    if (!q) {
      this.renderAppRepos(this.cachedRepos);
      return;
    }
    const filtered = this.cachedRepos.filter(r => 
      (r.name && r.name.toLowerCase().includes(q)) || 
      (r.description && r.description.toLowerCase().includes(q))
    );
    this.renderAppRepos(filtered);
  }

  renderAppRepos(repos) {
    const listContainer = document.getElementById('appsRepoList');
    if (!listContainer) return;

    listContainer.dataset.loaded = 'true';
    if (!repos || repos.length === 0) {
      listContainer.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 1.5rem; font-size: 0.85rem; color: var(--text-muted);">
          No repositories found. Start by generating an app below!
        </div>
      `;
      return;
    }

    listContainer.innerHTML = repos.map(repo => {
      const isPrivate = repo.is_private ? '<span style="font-size: 0.7rem; color: var(--accent-amber);" title="Private">🔒 Private</span>' : '<span style="font-size: 0.7rem; color: var(--accent-emerald);">🌐 Public</span>';
      const isSelected = this.selectedRepo === repo.name ? 'selected' : '';
      const desc = repo.description || 'No description provided.';
      const pagesLink = repo.pages_url ? `<a href="${repo.pages_url}" target="_blank" class="apps-repo-link-btn" onclick="event.stopPropagation()">🌐 Live Pages</a>` : '';
      const githubLink = `<a href="${repo.url}" target="_blank" class="apps-repo-link-btn" onclick="event.stopPropagation()">🐙 GitHub</a>`;

      return `
        <div class="apps-repo-card ${isSelected}" onclick="window.appsInterface.selectRepoForApp('${repo.name}')" title="Click to modify this repo with AI">
          <div class="apps-repo-name-row">
            <div class="apps-repo-name">📦 ${repo.name}</div>
            <div>${isPrivate}</div>
          </div>
          <div class="apps-repo-desc">${desc}</div>
          <div class="apps-repo-footer">
            <div class="apps-repo-links">
              ${pagesLink}
              ${githubLink}
            </div>
            <button type="button" class="apps-repo-select-btn" onclick="event.stopPropagation(); window.appsInterface.selectRepoForApp('${repo.name}')">
              ✏️ Modify
            </button>
          </div>
        </div>
      `;
    }).join('');
  }

  selectRepoForApp(repoName) {
    this.selectedRepo = repoName;
    const targetUser = this.githubUser || 'karnkeshav';
    const directive = `Modify repository ${targetUser}/${repoName}: `;

    const promptText = document.getElementById('promptText');
    if (promptText) {
      promptText.value = directive;
      promptText.focus();
    }

    const quickChips = document.getElementById('quickChipsContainer');
    if (quickChips) quickChips.style.display = 'flex';

    // Highlight card
    document.querySelectorAll('.apps-repo-card').forEach(card => {
      if (card.querySelector('.apps-repo-name')?.innerText?.includes(repoName)) {
        card.classList.add('selected');
      } else {
        card.classList.remove('selected');
      }
    });

    if (typeof showToast === 'function') {
      showToast(`📂 Selected repository for modification: ${targetUser}/${repoName}`);
    }
  }

  async generateApp() {
    const prompt = document.getElementById('appChatInput')?.value.trim();
    if (!prompt) {
      alert('Please describe the app you want to build or modify');
      return;
    }

    const mode = document.getElementById('appMode')?.value || 'prototype';
    const generateBtn = document.getElementById('generateAppBtn');
    const responseArea = document.getElementById('appResponse');

    // Disable button and show generating status
    if (generateBtn) generateBtn.disabled = true;
    if (responseArea) responseArea.innerHTML = '<div class="generating">⏳ AI Engine is generating and deploying your app to GitHub Pages...</div>';

    // 1. Try local backend endpoint first if available
    try {
      let apiFetchFunc = window.apiFetch;
      let targetUser = this.githubUser || 'karnkeshav';
      let token = this.githubToken || '';

      if (typeof apiFetchFunc === 'function') {
        const localResp = await apiFetchFunc('/api/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt,
            category: 'websites',
            github_user: targetUser,
            github_token: token
          })
        });

        if (localResp.ok) {
          const taskData = await localResp.json();
          if (taskData && taskData.task_id) {
            this.pollLocalMission(taskData.task_id);
            return;
          }
        }
      }
    } catch (e) {
      console.warn('Local execution failed, falling back to ready4launch engine:', e);
    }

    // 2. Fallback to ready4launch cloud engine
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
              this.loadRepos(true);
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

  async pollLocalMission(taskId) {
    const responseArea = document.getElementById('appResponse');
    const generateBtn = document.getElementById('generateAppBtn');

    const pollInterval = setInterval(async () => {
      try {
        let apiFetchFunc = window.apiFetch;
        let resp;
        if (typeof apiFetchFunc === 'function') {
          resp = await apiFetchFunc(`/api/status/${taskId}`);
        } else {
          resp = await fetch(`http://localhost:8000/api/status/${taskId}`);
        }

        if (!resp.ok) return;
        const task = await resp.json();

        if (responseArea && task.logs && task.logs.length > 0) {
          const latestLog = task.logs[task.logs.length - 1];
          responseArea.innerHTML = `<div class="status-message">${latestLog}</div>`;
        }

        if (task.status === 'COMPLETED' || task.status === 'DONE') {
          clearInterval(pollInterval);
          if (generateBtn) generateBtn.disabled = false;
          if (task.deliverable) {
            this.displaySuccess({
              liveUrl: task.deliverable.live_url || task.deliverable.url,
              repoUrl: task.deliverable.repo_url || `https://github.com/${this.githubUser || 'karnkeshav'}/${task.deliverable.repo_name || 'app'}`,
              repoName: task.deliverable.repo_name || 'app',
              owner: this.githubUser || 'karnkeshav',
              files: task.deliverable.files || ['index.html']
            });
          } else {
            if (responseArea) {
              responseArea.innerHTML = `<div class="success-card"><div class="success-header">✅ Completed: ${task.answer || 'App generated successfully!'}</div></div>`;
            }
          }
          this.loadRepos(true);
          if (typeof loadUserGitHubRepos === 'function') loadUserGitHubRepos(true);
        } else if (task.status === 'FAILED' || task.status === 'ERROR') {
          clearInterval(pollInterval);
          if (generateBtn) generateBtn.disabled = false;
          this.displayError({ message: task.answer || 'Mission execution encountered an error.' });
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 1500);
  }

  displaySuccess(data) {
    const responseArea = document.getElementById('appResponse');
    if (!responseArea) return;

    const html = `
      <div class="success-card">
        <div class="success-header">✅ App Generated & Deployed Successfully!</div>

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
            <tr><td>Files:</td><td>${(data.files && data.files.length) || 1} files deployed</td></tr>
          </table>
        </div>

        <div class="action-buttons">
          <button class="btn-secondary" onclick="window.open('${data.liveUrl}', '_blank')">Preview App</button>
          <button class="btn-secondary" onclick="window.open('${data.repoUrl}', '_blank')">Edit on GitHub</button>
          <button class="btn-secondary" onclick="if(window.appsInterface) window.appsInterface.loadRepos(true)">Refresh Repos</button>
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
      .then(async data => {
        if (data.access_token) {
          this.githubToken = data.access_token;
          localStorage.setItem('aiorch_github_token', data.access_token);
          localStorage.setItem('aios_github_token', data.access_token);
          
          // Fetch user profile
          try {
            const userRes = await fetch('https://api.github.com/user', {
              headers: { 'Authorization': `Bearer ${data.access_token}`, 'Accept': 'application/vnd.github.v3+json' }
            });
            if (userRes.ok) {
              const uData = await userRes.json();
              this.githubUser = uData.login;
              localStorage.setItem('aiorch_github_user', uData.login);
              localStorage.setItem('aios_github_user', uData.login);
            }
          } catch (e) {
            console.warn('Could not fetch user profile with token:', e);
          }

          this.updateAuthStatus();
          await this.loadRepos(true);
          if (typeof loadUserGitHubRepos === 'function') loadUserGitHubRepos(true);
          
          // Clean up URL
          window.history.replaceState({}, document.title, window.location.pathname);
        }
      })
      .catch(err => console.error('Auth callback error:', err));
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
