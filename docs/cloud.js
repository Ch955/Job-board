/* Job Radar cloud: email / Google sign-in and private sync, on Firebase's free plan.

   Each person's statuses live in one Firestore document, users/<their id>, that
   only they can read or write (see the security rules in the README). Changes on
   one device reach the others within seconds. app.js talks to this module only
   through window.JRCloud, so the rest of the app doesn't care where data lives. */

const SDK = "https://www.gstatic.com/firebasejs/10.12.2";
let A = null; // firebase/auth
let F = null; // firebase/firestore
let auth = null;
let db = null;

const JRCloud = {
  ready: false,

  async init(config) {
    const [appMod, authMod, fsMod] = await Promise.all([
      import(`${SDK}/firebase-app.js`),
      import(`${SDK}/firebase-auth.js`),
      import(`${SDK}/firebase-firestore.js`),
    ]);
    A = authMod;
    F = fsMod;
    const app = appMod.initializeApp(config);
    auth = A.getAuth(app);
    db = F.getFirestore(app);
    // Finish a Google sign-in that had to go through a full-page redirect
    await A.getRedirectResult(auth).catch(() => null);
    this.ready = true;
  },

  onUser(cb) {
    return A.onAuthStateChanged(auth, (u) => cb(u ? { uid: u.uid, email: u.email, name: u.displayName, photo: u.photoURL } : null));
  },

  async remember(keep) {
    await A.setPersistence(auth, keep ? A.browserLocalPersistence : A.browserSessionPersistence);
  },

  signIn: (email, password) => A.signInWithEmailAndPassword(auth, email, password),
  signUp: (email, password) => A.createUserWithEmailAndPassword(auth, email, password),
  resetPassword: (email) => A.sendPasswordResetEmail(auth, email),
  signOut: () => A.signOut(auth),

  async google() {
    const provider = new A.GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });
    try {
      return await A.signInWithPopup(auth, provider);
    } catch (e) {
      // Installed apps and some phones block pop-ups: fall back to a full-page redirect
      if (["auth/popup-blocked", "auth/operation-not-supported-in-this-environment", "auth/cancelled-popup-request"].includes(e.code)) {
        return A.signInWithRedirect(auth, provider);
      }
      throw e;
    }
  },

  // cb(state or null, fromThisDevice) whenever the document changes anywhere
  subscribe(uid, cb, onError) {
    return F.onSnapshot(F.doc(db, "users", uid), (snap) => {
      const data = snap.exists() ? snap.data() : null;
      cb(data && data.state ? JSON.parse(data.state) : null, snap.metadata.hasPendingWrites);
    }, onError);
  },

  // Read-merge-write in one transaction, so two devices saving at once both win
  async save(uid, email, mergeWith) {
    const ref = F.doc(db, "users", uid);
    return F.runTransaction(db, async (tx) => {
      const snap = await tx.get(ref);
      const remote = snap.exists() && snap.data().state ? JSON.parse(snap.data().state) : {};
      const merged = mergeWith(remote);
      tx.set(ref, { state: JSON.stringify(merged), email: email || "", updated: F.serverTimestamp() });
      return merged;
    });
  },
};

window.JRCloud = JRCloud;
window.dispatchEvent(new Event("jrcloud-loaded"));
