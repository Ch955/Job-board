// Firestore rules tests (emulator). Synthetic users only.
import { test, before, after, beforeEach } from "node:test";
import { readFileSync } from "node:fs";
import { initializeTestEnvironment, assertFails, assertSucceeds } from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc, deleteDoc } from "firebase/firestore";

let env;
before(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-jobradar",
    firestore: { rules: readFileSync(new URL("../../firestore.rules", import.meta.url), "utf8"), host: "127.0.0.1", port: 8080 },
  });
});
after(async () => env && env.cleanup());
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    for (const c of ["profiles", "matches", "gmail", "secrets", "resumes", "resume_chunks", "push", "oauth_states"]) {
      await setDoc(doc(db, c, "alice"), { x: 1 });
    }
    await setDoc(doc(db, "pool", "c0"), { jobs: [] });
    await setDoc(doc(db, "users", "alice"), { state: "{}", email: "alice@example.com" });
  });
});

const alice = () => env.authenticatedContext("alice", { email: "alice@example.com" }).firestore();
const bob = () => env.authenticatedContext("bob", { email: "bob@example.com" }).firestore();
const anon = () => env.unauthenticatedContext().firestore();

test("owner reads and writes their own synced state", async () => {
  await assertSucceeds(getDoc(doc(alice(), "users", "alice")));
  await assertSucceeds(setDoc(doc(alice(), "users", "alice"), { state: "{\"statuses\":{}}", email: "alice@example.com", updated: "x" }));
  await assertSucceeds(deleteDoc(doc(alice(), "users", "alice")));
});

test("nobody else reads or writes it", async () => {
  await assertFails(getDoc(doc(bob(), "users", "alice")));
  await assertFails(setDoc(doc(bob(), "users", "alice"), { state: "{}" }));
  await assertFails(getDoc(doc(anon(), "users", "alice")));
  await assertFails(setDoc(doc(anon(), "users", "anon"), { state: "{}" }));
});

test("state shape and size are enforced", async () => {
  await assertFails(setDoc(doc(alice(), "users", "alice"), { state: "{}", admin: true }));
  await assertFails(setDoc(doc(alice(), "users", "alice"), { state: { not: "a string" } }));
  await assertFails(setDoc(doc(alice(), "users", "alice"), { state: "x".repeat(900001) }));
  await assertFails(setDoc(doc(alice(), "users", "alice"), { state: "{}", email: "someone-else@example.com" }));
});

test("profile, matches and Gmail status: owner read only", async () => {
  for (const c of ["profiles", "matches", "gmail"]) {
    await assertSucceeds(getDoc(doc(alice(), c, "alice")));
    await assertFails(setDoc(doc(alice(), c, "alice"), { threshold: 0 }));
    await assertFails(getDoc(doc(bob(), c, "alice")));
  }
});

test("server-only collections are closed to every client", async () => {
  for (const c of ["secrets", "resumes", "resume_chunks", "push", "oauth_states"]) {
    await assertFails(getDoc(doc(alice(), c, "alice")));
    await assertFails(setDoc(doc(alice(), c, "alice"), { x: 2 }));
  }
  await assertFails(getDoc(doc(alice(), "pool", "c0")));
  await assertFails(getDoc(doc(anon(), "secrets", "alice")));
});
