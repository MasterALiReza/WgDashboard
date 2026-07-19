<template>
	<div class="global-backup-container">
		<header class="gb-header">
			<div class="gb-title-group">
				<h2 class="gb-title"><LocaleText t="GLOBAL_BACKUP_SYSTEM" /></h2>
				<span class="gb-status-badge" :class="{'active': !loading}">
					{{ loading ? GetLocale('SYNCING') : GetLocale('ONLINE') }}
				</span>
			</div>
			<p class="gb-subtitle">
				<LocaleText t="Critical system snapshot and restoration utility. Proceed with caution." />
			</p>
		</header>

		<div class="gb-controls">
			<button class="gb-btn gb-btn-primary" @click="createBackup()" :disabled="creating">
				<span v-if="creating" class="gb-loader"></span>
				<span v-else>[+] <LocaleText t="INITIATE_FULL_BACKUP" /></span>
			</button>
			
			<button class="gb-btn gb-btn-icon" @click="getBackups()" :disabled="loading" :title="GetLocale('Refresh')">
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square">
					<path d="M23 4v6h-6"></path>
					<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
				</svg>
			</button>
			
			<input type="file" ref="fileInput" @change="uploadBackup" accept=".zip" style="display: none;" />
			<button class="gb-btn gb-btn-icon" @click="$refs.fileInput.click()" :disabled="loading" :title="GetLocale('Upload Archive')">
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square">
					<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
					<polyline points="17 8 12 3 7 8"></polyline>
					<line x1="12" y1="3" x2="12" y2="15"></line>
				</svg>
			</button>
		</div>

		<div class="gb-data-view">
			<div v-if="loading && backups.length === 0" class="gb-empty-state">
				<span class="gb-loader-text"><LocaleText t="RETRIEVING_DATA..." /></span>
			</div>
			
			<div v-else-if="backups.length === 0" class="gb-empty-state">
				<div class="gb-empty-icon">∅</div>
				<p><LocaleText t="NO_ARCHIVES_FOUND" /></p>
			</div>

			<table v-else class="gb-table">
				<thead>
					<tr>
						<th><LocaleText t="ARCHIVE_ID" /></th>
						<th><LocaleText t="TIMESTAMP" /></th>
						<th><LocaleText t="VOLUME" /></th>
						<th class="text-right"><LocaleText t="OPERATIONS" /></th>
					</tr>
				</thead>
				<tbody>
					<tr v-for="backup in backups" :key="backup.filename">
						<td class="gb-mono gb-filename">{{ backup.filename }}</td>
						<td class="gb-mono">{{ formatDate(backup.date) }}</td>
						<td class="gb-mono">{{ formatSize(backup.size) }}</td>
						<td class="gb-actions text-right">
							<button class="gb-action-btn gb-btn-download" @click="downloadBackup(backup.filename)" :title="GetLocale('Download')">
								DL
							</button>
							<button class="gb-action-btn gb-btn-restore" @click="confirmRestore(backup.filename)" :title="GetLocale('Restore')">
								RST
							</button>
							<button class="gb-action-btn gb-btn-delete" @click="confirmDelete(backup.filename)" :title="GetLocale('Delete')">
								DEL
							</button>
						</td>
					</tr>
				</tbody>
			</table>
		</div>
	</div>
</template>

<script>
import LocaleText from "@/components/text/localeText.vue";
import {fetchGet, fetchPost, getUrl, getHeaders} from "@/utilities/fetch.js";
import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import {GetLocale} from "@/utilities/locale.js";

export default {
	name: "globalBackup",
	components: {LocaleText},
	setup() {
		const store = DashboardConfigurationStore();
		return {store};
	},
	data() {
		return {
			backups: [],
			loading: false,
			creating: false
		};
	},
	mounted() {
		this.getBackups();
	},
	methods: {
		GetLocale,
		formatSize(bytes) {
			if (bytes === 0) return '0 B';
			const k = 1024;
			const sizes = ['B', 'KB', 'MB', 'GB'];
			const i = Math.floor(Math.log(bytes) / Math.log(k));
			return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
		},
		formatDate(dateStr) {
			const d = new Date(dateStr);
			return d.toISOString().replace('T', ' ').substring(0, 19) + 'Z';
		},
		async getBackups() {
			this.loading = true;
			await fetchGet("/api/globalBackup/list", {}, (res) => {
				if (res.status) {
					this.backups = res.data.sort((a, b) => new Date(b.date) - new Date(a.date));
				} else {
					this.store.newMessage("WGDashboard", res.message, "danger");
				}
				this.loading = false;
			});
		},
		async createBackup() {
			this.creating = true;
			await fetchPost("/api/globalBackup/create", {}, (res) => {
				if (res.status) {
					this.store.newMessage("WGDashboard", "Global backup created successfully", "success");
					this.getBackups();
				} else {
					this.store.newMessage("WGDashboard", res.message, "danger");
				}
				this.creating = false;
			});
		},
		confirmDelete(filename) {
			if (confirm(this.GetLocale("Are you sure you want to delete this global backup?"))) {
				this.deleteBackup(filename);
			}
		},
		async deleteBackup(filename) {
			await fetchPost("/api/globalBackup/delete", {filename: filename}, (res) => {
				if (res.status) {
					this.store.newMessage("WGDashboard", "Backup deleted", "success");
					this.getBackups();
				} else {
					this.store.newMessage("WGDashboard", res.message, "danger");
				}
			});
		},
		downloadBackup(filename) {
			window.open(`/api/globalBackup/download?filename=${encodeURIComponent(filename)}`, '_blank');
		},
		confirmRestore(filename) {
			if (confirm(this.GetLocale("CRITICAL WARNING: Restoring a global backup will OVERWRITE your current database, configurations, and settings. The application will undergo a HARD RESTART. Proceed?"))) {
				this.restoreBackup(filename);
			}
		},
		async restoreBackup(filename) {
			this.store.newMessage("WGDashboard", "Restoring backup. The dashboard will restart shortly...", "warning");
			await fetchPost("/api/globalBackup/restore", {filename: filename}, (res) => {
				if (res.status) {
					this.store.newMessage("WGDashboard", "Restore initiated. System is rebooting.", "success");
					setTimeout(() => {
						window.location.reload();
					}, 5000);
				} else {
					this.store.newMessage("WGDashboard", res.message, "danger");
				}
			});
		},
		async uploadBackup(event) {
			const file = event.target.files[0];
			if (!file) return;
			if (!file.name.endsWith('.zip')) {
				this.store.newMessage("WGDashboard", "Only .zip files are supported", "danger");
				event.target.value = null;
				return;
			}
			if (confirm(this.GetLocale("CRITICAL WARNING: Restoring an uploaded backup will OVERWRITE your current database, configurations, and settings. The application will undergo a HARD RESTART. Proceed?"))) {
				this.store.newMessage("WGDashboard", "Uploading and restoring backup...", "warning");
				
				const formData = new FormData();
				formData.append('backupFile', file);
				
				try {
					const headers = getHeaders();
					delete headers['Content-Type']; // Let browser set multipart boundary
					
					const response = await fetch(getUrl("/api/globalBackup/restore"), {
						method: 'POST',
						headers: headers,
						body: formData
					});
					
					const res = await response.json();
					if (res.status) {
						this.store.newMessage("WGDashboard", "Restore initiated. System is rebooting.", "success");
						setTimeout(() => {
							window.location.reload();
						}, 5000);
					} else {
						this.store.newMessage("WGDashboard", res.message || "Failed to restore", "danger");
					}
				} catch (error) {
					console.error(error);
					this.store.newMessage("WGDashboard", "Error uploading file", "danger");
				}
			}
			event.target.value = null;
		}
	}
}
</script>

<style scoped>
/* Industrial Utilitarian Aesthetic */
/* Focuses on high contrast, harsh borders, monospaced data, and raw interactions */

.global-backup-container {
	--bg-primary: #ffffff;
	--border-color: #000000;
	--text-main: #000000;
	--text-muted: #666666;
	--accent-danger: #d32f2f;
	--accent-danger-hover: #b71c1c;
	--accent-warning: #f57c00;
	--accent-primary: #000000;
	--bg-hover: #f5f5f5;
	--font-mono: 'Courier New', Courier, monospace;
	--font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
	
	font-family: var(--font-sans);
	color: var(--text-main);
	background: var(--bg-primary);
	border: 2px solid var(--border-color);
	box-shadow: 4px 4px 0px var(--border-color); /* Harsh flat shadow */
	padding: 24px;
	margin-bottom: 2rem;
}

@media (prefers-color-scheme: dark) {
	.global-backup-container {
		--bg-primary: #121212;
		--border-color: #ffffff;
		--text-main: #ffffff;
		--text-muted: #a0a0a0;
		--bg-hover: #222222;
	}
}

.gb-header {
	border-bottom: 2px solid var(--border-color);
	padding-bottom: 16px;
	margin-bottom: 24px;
}

.gb-title-group {
	display: flex;
	justify-content: space-between;
	align-items: center;
}

.gb-title {
	font-weight: 800;
	font-size: 1.25rem;
	text-transform: uppercase;
	letter-spacing: -0.5px;
	margin: 0;
}

.gb-status-badge {
	font-family: var(--font-mono);
	font-size: 0.75rem;
	font-weight: bold;
	padding: 4px 8px;
	border: 1px solid var(--border-color);
	color: var(--border-color);
	text-transform: uppercase;
}

.gb-status-badge.active {
	background: var(--text-main);
	color: var(--bg-primary);
}

.gb-subtitle {
	margin: 8px 0 0 0;
	font-size: 0.85rem;
	color: var(--text-muted);
	font-family: var(--font-mono);
	text-transform: uppercase;
}

.gb-controls {
	display: flex;
	gap: 12px;
	margin-bottom: 24px;
}

.gb-btn {
	font-family: var(--font-sans);
	font-weight: 700;
	text-transform: uppercase;
	padding: 10px 16px;
	border: 2px solid var(--border-color);
	background: transparent;
	color: var(--text-main);
	cursor: pointer;
	transition: all 0.1s;
	display: flex;
	align-items: center;
	justify-content: center;
}

.gb-btn:disabled {
	opacity: 0.5;
	cursor: not-allowed;
	background: repeating-linear-gradient(
		45deg,
		transparent,
		transparent 10px,
		var(--bg-hover) 10px,
		var(--bg-hover) 20px
	);
}

.gb-btn:not(:disabled):hover {
	background: var(--text-main);
	color: var(--bg-primary);
}

.gb-btn-primary {
	flex-grow: 1;
}

.gb-btn-icon {
	padding: 10px;
}

.gb-data-view {
	border: 2px solid var(--border-color);
}

.gb-empty-state {
	padding: 48px;
	text-align: center;
	font-family: var(--font-mono);
	text-transform: uppercase;
	color: var(--text-muted);
}

.gb-empty-icon {
	font-size: 3rem;
	margin-bottom: 16px;
	line-height: 1;
}

.gb-loader {
	display: inline-block;
	width: 16px;
	height: 16px;
	border: 2px solid currentColor;
	border-right-color: transparent;
	animation: gb-spin 0.75s linear infinite;
}

.gb-loader-text {
	animation: gb-blink 1s steps(2, start) infinite;
}

@keyframes gb-spin {
	to { transform: rotate(360deg); }
}

@keyframes gb-blink {
	to { visibility: hidden; }
}

.gb-table {
	width: 100%;
	border-collapse: collapse;
}

.gb-table th, .gb-table td {
	padding: 12px 16px;
	border-bottom: 1px solid var(--border-color);
	text-align: left;
}

.gb-table th {
	font-size: 0.75rem;
	text-transform: uppercase;
	letter-spacing: 1px;
	font-weight: 700;
	background: var(--bg-hover);
}

.gb-table tbody tr:hover {
	background: var(--bg-hover);
}

.gb-mono {
	font-family: var(--font-mono);
	font-size: 0.85rem;
}

.gb-filename {
	font-weight: bold;
}

.gb-actions {
	display: flex;
	gap: 8px;
	justify-content: flex-end;
}

.gb-action-btn {
	font-family: var(--font-mono);
	font-size: 0.7rem;
	font-weight: bold;
	padding: 4px 8px;
	background: transparent;
	border: 1px solid var(--border-color);
	color: var(--text-main);
	cursor: pointer;
}

.gb-action-btn:hover {
	background: var(--text-main);
	color: var(--bg-primary);
}

.gb-btn-delete:hover {
	background: var(--accent-danger);
	border-color: var(--accent-danger);
	color: white;
}

.gb-btn-restore:hover {
	background: var(--accent-warning);
	border-color: var(--accent-warning);
	color: white;
}

.text-right {
	text-align: right !important;
}
</style>
