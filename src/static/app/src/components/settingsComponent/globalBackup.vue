<template>
	<div class="card mb-4">
		<div class="card-header d-flex justify-content-between align-items-center">
			<h6 class="my-2">
				<LocaleText t="Global Backup System" />
			</h6>
			<span class="badge" :class="{'text-bg-warning': loading, 'text-bg-success': !loading}">
				{{ loading ? GetLocale('SYNCING') : GetLocale('ONLINE') }}
			</span>
		</div>
		<div class="card-body">
			<p class="text-muted small mb-4">
				<LocaleText t="Critical system snapshot and restoration utility. Proceed with caution." />
			</p>

			<!-- ─── Restore Progress Bar (inside card) ─── -->
			<transition name="restore-fade">
				<div v-if="restoring" class="mb-4 p-3 rounded-4 border" style="background: var(--bs-tertiary-bg);">
					<div class="d-flex align-items-center justify-content-between mb-2">
						<div class="d-flex align-items-center gap-2">
							<span class="spinner-border spinner-border-sm text-warning" v-if="restoreProgress < 100"></span>
							<i class="bi bi-check-circle-fill text-success" v-else></i>
							<span class="fw-semibold small">
								<LocaleText t="Restoring Backup..." />
							</span>
						</div>
						<span class="badge text-bg-warning fw-bold">{{ restoreProgress }}%</span>
					</div>

					<!-- Progress Bar -->
					<div class="progress rounded-3 mb-2" style="height: 18px;">
						<div
							class="progress-bar progress-bar-striped progress-bar-animated bg-warning"
							role="progressbar"
							:style="{ width: restoreProgress + '%' }"
							:aria-valuenow="restoreProgress"
							aria-valuemin="0"
							aria-valuemax="100"
						></div>
					</div>

					<!-- Step label -->
					<small class="text-muted d-block">
						<i class="bi bi-arrow-right-circle me-1"></i>{{ restoreStepLabel }}
					</small>

					<!-- Warning: do not close -->
					<small class="text-danger d-block mt-2" v-if="restoreProgress < 100">
						<i class="bi bi-exclamation-triangle-fill me-1"></i>
						<LocaleText t="Do not close this page. The dashboard will restart automatically when done." />
					</small>
					<small class="text-success d-block mt-2" v-else>
						<i class="bi bi-arrow-repeat me-1"></i>
						<LocaleText t="Restore complete! Restarting dashboard..." />
					</small>
				</div>
			</transition>

			<div class="d-flex flex-column flex-sm-row gap-2 mb-4">
				<div class="flex-grow-1">
					<button class="btn rounded-3 w-100" :class="creating ? 'btn-secondary' : 'btn-primary'" @click="createBackup()" :disabled="creating || restoring">
						<span v-if="creating" class="spinner-border spinner-border-sm"></span>
						<span v-else><i class="bi bi-plus-circle me-1"></i> <LocaleText t="Create Global Backup" /></span>
					</button>
				</div>
				
				<div class="d-flex gap-2">
					<input type="file" ref="fileInput" @change="uploadBackup" accept=".zip" style="display: none;" />
					<button class="btn btn-outline-primary rounded-3 text-nowrap px-3" @click="$refs.fileInput.click()" :disabled="loading || restoring" :title="GetLocale('Upload Archive')">
						<i class="bi bi-upload me-1"></i> <LocaleText t="Upload Archive" />
					</button>
					<button class="btn btn-outline-secondary rounded-3 px-3" @click="getBackups()" :disabled="loading || restoring" :title="GetLocale('Refresh')">
						<i class="bi bi-arrow-clockwise"></i>
					</button>
					<button class="btn btn-outline-secondary rounded-3 px-3" @click="toggleSort()" :disabled="loading || restoring" :title="sort_descending ? GetLocale('Sort: Oldest First') : GetLocale('Sort: Newest First')">
						<i class="bi" :class="sort_descending ? 'bi-sort-numeric-down-alt' : 'bi-sort-numeric-up'"></i>
					</button>
				</div>
			</div>

			<!-- Auto Backup Settings -->
			<div class="card bg-body-tertiary mb-4 border-0 rounded-4">
				<div class="card-body">
					<div class="d-flex align-items-center justify-content-between mb-3">
						<div class="d-flex align-items-center gap-2">
							<i class="bi bi-clock-history fs-5 text-primary"></i>
							<h6 class="mb-0 fw-bold"><LocaleText t="Automatic Backup" /></h6>
						</div>
						<div class="form-check form-switch m-0 fs-5">
							<input class="form-check-input" type="checkbox" role="switch" v-model="auto_backup" @change="saveSettings" :disabled="saving_settings">
						</div>
					</div>
					<div class="row align-items-center" :class="{'opacity-50 pe-none': !auto_backup}">
						<div class="col-sm-4 text-muted small fw-medium">
							<LocaleText t="Schedule Interval" />
						</div>
						<div class="col-sm-8">
							<select class="form-select form-select-sm border-0 bg-body rounded-3" v-model="auto_backup_schedule" @change="saveSettings" :disabled="!auto_backup || saving_settings">
								<option value="3h">3 Hours</option>
								<option value="6h">6 Hours</option>
								<option value="12h">12 Hours</option>
								<option value="daily">Daily</option>
								<option value="weekly">Weekly</option>
								<option value="monthly">Monthly</option>
							</select>
						</div>
					</div>
					<div class="mt-3 text-muted opacity-75" style="font-size: 0.75rem;">
						<i class="bi bi-info-circle-fill me-1"></i>
						<LocaleText t="Retention Policy: Maximum 10 automated backups will be kept. Older backups will be overwritten to save disk space." />
					</div>
				</div>
			</div>

			<div class="table-responsive">
				<div v-if="loading && backups.length === 0" class="text-center py-5 text-muted">
					<span class="spinner-border spinner-border-sm me-2"></span>
					<LocaleText t="Retrieving data..." />
				</div>
				
				<div v-else-if="backups.length === 0" class="text-center py-5 text-muted">
					<i class="bi bi-inbox fs-1 d-block mb-3"></i>
					<small><strong><LocaleText t="No backup archives found" /></strong></small>
				</div>

				<table v-else class="table table-hover align-middle mb-0">
					<thead>
						<tr>
							<th class="text-muted border-0"><small><LocaleText t="Archive Name" /></small></th>
							<th class="text-muted border-0"><small><LocaleText t="Date Created" /></small></th>
							<th class="text-muted border-0"><small><LocaleText t="File Size" /></small></th>
							<th class="text-end text-muted border-0"><small><LocaleText t="Actions" /></small></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="backup in sortedBackups" :key="backup.filename">
							<td><small class="fw-bold">{{ backup.filename }}</small></td>
							<td><small class="text-muted"><i class="bi bi-calendar3 me-1"></i> {{ formatDate(backup.created) }}</small></td>
							<td><small class="text-muted"><i class="bi bi-hdd me-1"></i> {{ formatSize(backup.size) }}</small></td>
							<td class="text-end">
								<div class="btn-group">
									<button class="btn btn-sm btn-outline-primary" @click="downloadBackup(backup.filename)" :title="GetLocale('Download')" :disabled="restoring">
										<i class="bi bi-download"></i>
									</button>
									<button class="btn btn-sm btn-outline-warning" @click="confirmRestore(backup.filename)" :title="GetLocale('Restore')" :disabled="restoring">
										<i class="bi bi-arrow-counterclockwise"></i>
									</button>
									<button class="btn btn-sm btn-outline-danger" @click="confirmDelete(backup.filename)" :title="GetLocale('Delete')" :disabled="restoring">
										<i class="bi bi-trash"></i>
									</button>
								</div>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
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
			creating: false,
			auto_backup: false,
			auto_backup_schedule: 'daily',
			saving_settings: false,
			sort_descending: true,
			// ─── Restore Progress ───────────────────────
			restoring: false,
			restoreProgress: 0,
			restoreStepLabel: '',
			_restoreEventSource: null,
		};
	},
	mounted() {
		this.getBackups();
		this.getSettings();
	},
	beforeUnmount() {
		this._closeSSE();
	},
	computed: {
		sortedBackups() {
			if (this.sort_descending) {
				return this.backups.slice().sort((a, b) => new Date(b.created) - new Date(a.created));
			} else {
				return this.backups.slice().sort((a, b) => new Date(a.created) - new Date(b.created));
			}
		}
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
				if (!dateStr) return 'N/A';
				const d = new Date(dateStr);
				if (isNaN(d.getTime())) return dateStr;
				// Display as: Sun, 20 Jul 2026 · 14:30:45
				const datePart = d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
				const timePart = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
				return `${datePart} · ${timePart}`;
			},
		toggleSort() {
			this.sort_descending = !this.sort_descending;
		},
		async getSettings() {
			await fetchGet("/api/globalBackup/settings", {}, (res) => {
				if (res.status && res.data) {
					this.auto_backup = res.data.auto_backup;
					this.auto_backup_schedule = res.data.auto_backup_schedule;
				}
			});
		},
		async saveSettings() {
			this.saving_settings = true;
			// Save toggle
			await fetchPost("/api/updateDashboardConfigurationItem", {
				section: "GlobalBackup",
				key: "auto_backup",
				value: this.auto_backup
			}, () => {});
			// Save schedule
			await fetchPost("/api/updateDashboardConfigurationItem", {
				section: "GlobalBackup",
				key: "auto_backup_schedule",
				value: this.auto_backup_schedule
			}, () => {});
			this.store.newMessage("WGDashboard", "Auto Backup settings saved", "success");
			this.saving_settings = false;
		},
		async getBackups() {
			this.loading = true;
			await fetchGet("/api/globalBackup/list", {}, (res) => {
				if (res.status) {
					this.backups = res.data;
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
				this.restoreBackup({filename});
			}
		},

		// ─── Restore Progress Helpers ─────────────────────────────────
		_closeSSE() {
			if (this._restoreEventSource) {
				this._restoreEventSource.close();
				this._restoreEventSource = null;
			}
		},
		_getStepLabel(step) {
			const labels = {
				uploading:           'Uploading backup archive...',
				queued:              'Waiting to start restore...',
				validating:          'Validating backup archive...',
				extracting:          'Extracting backup files...',
				restoring_configs:   'Restoring configuration files...',
				restoring_wireguard: 'Restoring WireGuard interfaces...',
				restoring_databases: 'Restoring databases & records...',
				finalizing:          'Finalizing restore operation...',
				done:                'Restore complete! Restarting dashboard...',
			};
			return labels[step] || (step ? step : 'Processing...');
		},
		_beginProgressTracking(jobId) {
			this._closeSSE();

			const url = getUrl(`/api/globalBackup/restore/progress?job_id=${encodeURIComponent(jobId)}`);
			const es = new EventSource(url);
			this._restoreEventSource = es;

			es.onmessage = (event) => {
				let data;
				try { data = JSON.parse(event.data); } catch { return; }

				this.restoreProgress = data.pct || 0;
				this.restoreStepLabel = this._getStepLabel(data.step);

				if (data.error) {
					this._closeSSE();
					this.restoring = false;
					this.store.newMessage("WGDashboard", data.error, "danger");
					return;
				}

				if (data.done) {
					this.restoreProgress = 100;
					this.restoreStepLabel = this._getStepLabel('done');
					this._closeSSE();
					// Dashboard restarts via SIGTERM after 3s (server side).
					// Keep polling until panel is back up, then reload.
					this._waitForPanelRestart();
				}
			};

			es.onerror = () => {
				// SSE connection dropped — likely because the server is restarting.
				this._closeSSE();
				if (this.restoreProgress >= 95) {
					// Looks like restore succeeded and server is restarting — wait and reload.
					this._waitForPanelRestart();
				} else if (this.restoring) {
					this.restoring = false;
					this.store.newMessage("WGDashboard", "Connection lost during restore. Please check server logs.", "danger");
				}
			};
		},
		_waitForPanelRestart() {
			// Poll /api/getDashboardConfiguration every 2 seconds until server is back.
			this.restoreStepLabel = 'Waiting for panel to restart...';
			const maxAttempts = 60; // up to 2 min
			let attempts = 0;
			const poll = () => {
				fetch('/api/getDashboardConfiguration', { method: 'GET' })
					.then(r => {
						if (r.ok) {
							window.location.reload();
						} else {
							throw new Error('not ready');
						}
					})
					.catch(() => {
						attempts++;
						if (attempts < maxAttempts) {
							setTimeout(poll, 2000);
						} else {
							// Give up, just reload anyway
							window.location.reload();
						}
					});
			};
			// Wait 3 seconds before first poll (give server time to start shutting down)
			setTimeout(poll, 3000);
		},

		// ─── Restore entry points ─────────────────────────────────────
		async restoreBackup({filename = null, formData = null} = {}) {
			this.restoring = true;
			this.restoreProgress = 0;

			try {
				let res;
				if (formData) {
					// Uploaded file — XHR for real-time upload progress
					this.restoreStepLabel = 'Uploading backup file... (0%)';
					res = await new Promise((resolve, reject) => {
						const xhr = new XMLHttpRequest();
						xhr.open("POST", getUrl("/api/globalBackup/restore"));

						const headers = getHeaders();
						delete headers['Content-Type']; // Let browser set multipart boundary

						for (const [k, v] of Object.entries(headers)) {
							xhr.setRequestHeader(k, v);
						}

						xhr.upload.onprogress = (e) => {
							if (e.lengthComputable) {
								const pct = Math.round((e.loaded * 100) / e.total);
								this.restoreProgress = pct;
								this.restoreStepLabel = `Uploading backup file... (${pct}%)`;
							}
						};

						xhr.onload = () => {
							try {
								const json = JSON.parse(xhr.responseText);
								resolve(json);
							} catch (err) {
								reject(new Error("Invalid server response during upload"));
							}
						};

						xhr.onerror = () => reject(new Error("Network error during file upload"));
						xhr.send(formData);
					});
				} else {
					// Existing backup — JSON POST
					this.restoreStepLabel = this._getStepLabel('queued');
					res = await new Promise((resolve) => {
						fetchPost("/api/globalBackup/restore", {filename}, (r) => resolve(r));
					});
				}

				if (!res.status) {
					this.restoring = false;
					this.store.newMessage("WGDashboard", res.message || "Failed to start restore", "danger");
					return;
				}

				const jobId = res.data?.job_id;
				if (!jobId) {
					this.restoring = false;
					this.store.newMessage("WGDashboard", "No job_id returned from server", "danger");
					return;
				}

				// Switch to tracking backend restore progress via SSE
				this.restoreProgress = 0;
				this._beginProgressTracking(jobId);

			} catch (error) {
				console.error(error);
				this.restoring = false;
				this.store.newMessage("WGDashboard", error.message || "Error starting restore", "danger");
			}
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
				const formData = new FormData();
				formData.append('backupFile', file);
				await this.restoreBackup({formData});
			}
			event.target.value = null;
		}
	}
}
</script>

<style scoped>
.restore-fade-enter-active,
.restore-fade-leave-active {
	transition: opacity 0.4s ease, transform 0.4s ease;
}
.restore-fade-enter-from,
.restore-fade-leave-to {
	opacity: 0;
	transform: translateY(-8px);
}
</style>
