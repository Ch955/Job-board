/* Job Radar inside the Android app (Capacitor). Does nothing in a normal browser.

   - Gmail consent opens in the system browser (Google doesn't allow it inside an
     app's web view) and comes back through the app.jobradar://gmail link.
   - Google sign-in uses Android's own account picker, then signs in to Firebase. */
"use strict";

window.JRNative = (() => {
  const C = window.Capacitor;
  const isApp = !!(C && C.isNativePlatform && C.isNativePlatform());
  if (!isApp) return { isApp: false };
  const P = C.Plugins || {};

  P.App?.addListener("appUrlOpen", ({ url }) => {
    if (!url || !url.startsWith("app.jobradar://gmail")) return;
    P.Browser?.close().catch(() => {});
    const q = url.split("?")[1] || "";
    history.replaceState(history.state, "", `${location.pathname}#${q}`);
    window.JRAccount?.handleReturn();
  });

  return {
    isApp: true,
    openExternal: (url) => P.Browser.open({ url }),
    async googleIdToken() {
      const r = await P.FirebaseAuthentication.signInWithGoogle();
      const token = r?.credential?.idToken;
      if (!token) throw Object.assign(new Error("Google sign-in was cancelled."), { code: "auth/popup-closed-by-user" });
      return token;
    },
  };
})();
