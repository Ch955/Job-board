// Copy the web app (../docs) into www/ for Capacitor, without the public jobs.json
// (the Android app is for signed-in users whose matches come from the API).
import { cpSync, rmSync, mkdirSync, existsSync } from "node:fs";
const src = new URL("../../docs/", import.meta.url);
const out = new URL("../www/", import.meta.url);
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
cpSync(src, out, { recursive: true, filter: (p) => !/jobs\.json$|templates\.json$/.test(p) });
if (!existsSync(new URL("app-config.json", out))) throw new Error("docs/app-config.json missing");
console.log("Copied docs/ to mobile/www/");
