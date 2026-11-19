// AutoX.js v6 (Rhino). Set the station URL and this Android device's Key.
var SITE_URL = "";
var DEVICE_KEY = "";
var COLLECT_APP = false;

function collect() {
    var keyguard = context.getSystemService(android.content.Context.KEYGUARD_SERVICE);
    var using = device.isScreenOn() && !keyguard.isKeyguardLocked();
    var status = null;
    if (using && COLLECT_APP && auto.service) {
        var pkg = currentPackage();
        if (pkg) {
            status = String(app.getAppName(pkg) || pkg).slice(0, 512);
        }
    }
    var battery = device.getBattery();
    return {
        using: Boolean(using),
        status: status,
        battery_percent: battery >= 0 && battery <= 100 ? Number(battery) : null,
        charging: Boolean(device.isCharging())
    };
}

function report(url, payload) {
    var connection = url.openConnection();
    try {
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(10000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Authorization", "Bearer " + DEVICE_KEY);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        connection.setDoOutput(true);
        var output = connection.getOutputStream();
        try {
            output.write(new java.lang.String(payload).getBytes("UTF-8"));
        } finally {
            output.close();
        }
        return Number(connection.getResponseCode());
    } finally {
        connection.disconnect();
    }
}

function main() {
    if (!/^https?:\/\/[^\s/?#@]+\/?$/i.test(SITE_URL) || !DEVICE_KEY.trim() || /[\r\n]/.test(DEVICE_KEY)) {
        throw new Error("请先填写站点根地址 SITE_URL 和该设备的 DEVICE_KEY。");
    }
    var url = new java.net.URL(SITE_URL.replace(/\/$/, "") + "/api/device/report");
    var previous = "";
    var last = 0;
    var retryDelay = 2000;
    while (true) {
        var payload = JSON.stringify(collect());
        var now = android.os.SystemClock.elapsedRealtime();
        if (payload !== previous || now - last >= 30000) {
            var code;
            try {
                code = report(url, payload);
            } catch (error) {
                code = 0;
            }
            if (code === 401 || code === 403 || code === 422) {
                throw new Error("上报被拒绝（HTTP " + code + "），请检查设备 Key、启用状态和字段。");
            }
            if (code !== 204) {
                console.warn("上报未成功，" + retryDelay / 1000 + " 秒后重试");
                sleep(retryDelay);
                retryDelay = Math.min(retryDelay * 2, 60000);
                continue;
            }
            last = now;
            previous = payload;
            retryDelay = 2000;
        }
        sleep(2000);
    }
}

main();
