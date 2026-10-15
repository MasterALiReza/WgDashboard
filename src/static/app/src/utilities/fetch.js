import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import router from "@/router/router.js";
export const getHeaders = () => {
	let headers = {
		"Content-Type": "application/json"
	}
	const store = DashboardConfigurationStore();
	const crossServer = store.getActiveCrossServer();
	if (crossServer){
		headers['wg-dashboard-apikey'] = crossServer.apiKey
        if (crossServer.headers){
            for (let header of Object.values(crossServer.headers)){
                if (header.key && header.value && !Object.keys(headers).includes(header.key)){
                    headers[header.key] = header.value
                }
            }
        }
	}


	return headers
}

export const getUrl = (url) => {
	const store = DashboardConfigurationStore();
	const apiKey = store.getActiveCrossServer();
	if (apiKey){
		return `${apiKey.host}${url}`
	}
	if (import.meta.env.MODE === 'development') {
		return url;
	}
	// const appPrefix = window.APP_PREFIX || '';
	return `./.${url}`;
}

export const fetchGet = async (url, params=undefined, callback=undefined) => {
	if (typeof params === 'function') {
		callback = params;
		params = undefined;
	}
	let fullUrl = getUrl(url);
	if (params && Object.keys(params).length > 0) {
		const urlSearchParams = new URLSearchParams(params);
		fullUrl = `${fullUrl}?${urlSearchParams.toString()}`;
	}
	try {
		const x = await fetch(fullUrl, {
			headers: getHeaders()
		});
		let data = null;
		try {
			data = await x.json();
		} catch {
			data = { status: false, message: `HTTP ${x.status}` };
		}
		if (data && typeof data === 'object' && !('status' in data) && !x.ok) {
			data.status = false;
		}
		if (x.status === 401) {
			DashboardConfigurationStore().newMessage("WGDashboard", "Sign in session ended, please sign in again", "warning");
			router.push({ path: '/signin' });
		}
		if (x.status === 503) {
			DashboardConfigurationStore().newMessage("WGDashboard", data?.message || "Maintenance in progress", "warning");
		}
		if (callback) callback(data);
	} catch (e) {
		console.error("fetchGet error:", e);
		if (callback) callback({ status: false, message: "Network error" });
	}
}

export const fetchPost = async (url, body, callback=undefined) => {
	try {
		const x = await fetch(getUrl(url), {
			headers: getHeaders(),
			method: "POST",
			body: JSON.stringify(body)
		});
		let data = null;
		try {
			data = await x.json();
		} catch {
			data = { status: false, message: `HTTP ${x.status}` };
		}
		if (data && typeof data === 'object' && !('status' in data) && !x.ok) {
			data.status = false;
		}
		if (x.status === 401) {
			DashboardConfigurationStore().newMessage("WGDashboard", "Sign in session ended, please sign in again", "warning");
			router.push({ path: '/signin' });
		}
		if (x.status === 503) {
			DashboardConfigurationStore().newMessage("WGDashboard", data?.message || "Maintenance in progress", "warning");
		}
		if (callback) callback(data);
	} catch (e) {
		console.error("fetchPost error:", e);
		if (callback) callback({ status: false, message: "Network error" });
	}
}