<template>
	<div class="card mb-4">
		<div class="card-header d-flex justify-content-between align-items-center">
			<h6 class="my-2">
				<LocaleText t="GLOBAL_BACKUP_SYSTEM" />
			</h6>
			<span class="badge" :class="{'text-bg-warning': loading, 'text-bg-success': !loading}">
				{{ loading ? GetLocale('SYNCING') : GetLocale('ONLINE') }}
			</span>
		</div>
		<div class="card-body">
			<p class="text-muted small mb-4">
				<LocaleText t="Critical system snapshot and restoration utility. Proceed with caution." />
			</p>

			<div class="d-flex flex-column flex-sm-row gap-2 mb-4">
				<div class="flex-grow-1">
					<button class="btn rounded-3 w-100" :class="creating ? 'btn-secondary' : 'btn-primary'" @click="createBackup()" :disabled="creating">
						<span v-if="creating" class="spinner-border spinner-border-sm"></span>
						<span v-else><i class="bi bi-plus-circle me-1"></i> <LocaleText t="INITIATE_FULL_BACKUP" /></span>
					</button>
				</div>
				
				<div class="d-flex gap-2">
					<input type="file" ref="fileInput" @change="uploadBackup" accept=".zip" style="display: none;" />
					<button class="btn btn-outline-primary rounded-3 text-nowrap px-3" @click="$refs.fileInput.click()" :disabled="loading" :title="GetLocale('Upload Archive')">
						<i class="bi bi-upload me-1"></i> <LocaleText t="Upload Archive" />
					</button>
					<button class="btn btn-outline-secondary rounded-3 px-3" @click="getBackups()" :disabled="loading" :title="GetLocale('Refresh')">
						<i class="bi bi-arrow-clockwise"></i>
					</button>
					<button class="btn btn-outline-secondary rounded-3 px-3" @click="toggleSort()" :disabled="loading" :title="sort_descending ? GetLocale('Sort: Oldest First') : GetLocale('Sort: Newest First')">
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
					<LocaleText t="RETRIEVING_DATA..." />
				</div>
				
				<div v-else-if="backups.length === 0" class="text-center py-5 text-muted">
					<i class="bi bi-inbox fs-1 d-block mb-3"></i>
					<small><strong><LocaleText t="NO_ARCHIVES_FOUND" /></strong></small>
				</div>

				<table v-else class="table table-hover align-middle mb-0">
					<thead>
						<tr>
							<th class="text-muted border-0"><small><LocaleText t="ARCHIVE_ID" /></small></th>
							<th class="text-muted border-0"><small><LocaleText t="TIMESTAMP" /></small></th>
							<th class="text-muted border-0"><small><LocaleText t="VOLUME" /></small></th>
							<th class="text-end text-muted border-0"><small><LocaleText t="OPERATIONS" /></small></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="backup in sortedBackups" :key="backup.filename">
							<td><small class="fw-bold">{{ backup.filename }}</small></td>
							<td><small class="text-muted"><i class="bi bi-calendar3 me-1"></i> {{ formatDate(backup.created) }}</small></td>
							<td><small class="text-muted"><i class="bi bi-hdd me-1"></i> {{ formatSize(backup.size) }}</small></td>
							<td class="text-end">
								<div class="btn-group">
									<button class="btn btn-sm btn-outline-primary" @click="downloadBackup(backup.filename)" :title="GetLocale('Download')">
										<i class="bi bi-download"></i>
									</button>
									<button class="btn btn-sm btn-outline-warning" @click="confirmRestore(backup.filename)" :title="GetLocale('Restore')">
										<i class="bi bi-arrow-counterclockwise"></i>
									</button>
									<button class="btn btn-sm btn-outline-danger" @click="confirmDelete(backup.filename)" :title="GetLocale('Delete')">
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
			sort_descending: true
		};
	},
	mounted() {
		this.getBackups();
		this.getSettings();
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
			const d = new Date(dateStr);
			// Display as: Sun, 20 Jul 2026 · 14:30:45 UTC
			const options = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' };
			return d.toLocaleDateString('en-US', options).replace(',', '').replace(' PM', '').replace(' AM', ''); 
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

</style>
