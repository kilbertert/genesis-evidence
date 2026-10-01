/**
 * Runnable regression check for profile network selection.
 *
 * Run: `npx tsx .sandcastle/profile-network.check.ts`
 *
 * Asserts the property that decides whether the sandbox is isolated: only the
 * host-loopback profiles get `--network host`; everything else stays on the
 * default bridge. Without this, a typo in the set silently widens the sandbox's
 * reach — or silently breaks the relay profile.
 */
import { LOOPBACK_PROFILES, networkFor } from "./profile-network.js";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`profile-network check failed: ${message}`);
}

// The relay profile reaches host loopback, so it must share the host network.
assert(networkFor("claude-deepseek") === "host", "claude-deepseek must use host networking");

// Public-HTTPS profiles must stay on the default bridge — host networking would
// hand them the host's loopback for no reason.
assert(networkFor("claude-stepfun") === undefined, "claude-stepfun must stay on the default bridge");
assert(networkFor("claude") === undefined, "claude must stay on the default bridge");
assert(networkFor(undefined) === undefined, "no profile means the default bridge");

// The set and the function must agree — a drift here is the whole bug class.
for (const profile of LOOPBACK_PROFILES) {
  assert(networkFor(profile) === "host", `set member ${profile} must map to host networking`);
}
assert(
  [...LOOPBACK_PROFILES].every((p) => p === "claude-deepseek" || p.endsWith("-deepseek")),
  "loopback profiles should be the relay-backed ones",
);

console.log("profile-network check ok");
