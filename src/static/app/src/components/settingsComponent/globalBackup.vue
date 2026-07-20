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

			<div class="d-flex gap-2 mb-4 align-items-start">
				<div class="flex-grow-1">
					<button class="btn rounded-3 w-100" :class="creating ? 'btn-secondary' : 'btn-primary'" @click="createBackup()" :disabled="creating">
						<span v-if="creating" class="spinner-border spinner-border-sm"></span>
						<span v-else><i class="bi bi-plus-circle me-1"></i> <LocaleText t="INITIATE_FULL_BACKUP" /></span>
					</button>
				</div>
				
				<button class="btn btn-outline-secondary rounded-3" @click="getBackups()" :disabled="loading" :title="GetLocale('Refresh')">
					<i class="bi bi-arrow-clockwise"></i>
				</button>
				
				<input type="file" ref="fileInput" @change="uploadBackup" accept=".zip" style="display: none;" />
				<button class="btn btn-outline-secondary rounded-3" @click="$refs.fileInput.click()" :disabled="loading" :title="GetLocale('Upload Archive')">
					<i class="bi bi-upload"></i>
				</button>
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
						<tr v-for="backup in backups" :key="backup.filename">
							<td><small class="fw-bold">{{ backup.filename }}</small></td>
							<td><small class="text-muted">{{ formatDate(backup.date) }}</small></td>
							<td><small class="text-muted">{{ formatSize(backup.size) }}</small></td>
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

</style>
