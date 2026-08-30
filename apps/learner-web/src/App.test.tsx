import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import App from "./App";
import type { CapabilityStatus, Job, LocalSession } from "./api";

const status: CapabilityStatus = {
  controller: { ready: true, version: "test" },
  paths: {
    project: "project",
    trajectory: "trajectory.jsonl",
    checkpoints: "checkpoints",
  },
  sdk: { ready: true },
  torch: { ready: true },
  engine: {
    source_available: false,
    revision: null,
    pinned_revision: "engine-pinned",
    synced: false,
    dirty: false,
    built: false,
    url: "http://127.0.0.1:8787",
    healthy: false,
  },
  pixi: {
    source_available: false,
    built: false,
    build_present: false,
    built_revision: null,
    revision: null,
    pinned_revision: "pixi-pinned",
    synced: false,
    dirty: false,
  },
  hosted: {
    api_key_configured: false,
    trajectory_training: false,
    reason: "Not available yet.",
  },
  workflows: {},
};

const stackJob: Job = {
  id: "stack-job",
  kind: "dependency.stack.prepare",
  label: "Local Engine + Pixi setup",
  argv: [],
  status: "queued",
  created_at: "2026-08-29T00:00:00Z",
  started_at: null,
  finished_at: null,
  exit_code: null,
  artifact_path: null,
  logs: [],
};

function response(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

const ownerSession = {
  id: "owner-session",
  label: "This computer",
  role: "owner" as const,
  created_at: "2026-08-29T00:00:00Z",
};

const settings = {
  network: { mode: "lan", port: 8765, restart_required: false, lan_urls: [] },
  account: { configured: false, provider: null, externally_managed: false },
  access: { role: "owner" },
};

const profile = { model: "v12", format: "legacy", decks: [] };

function route(input: RequestInfo | URL, session: LocalSession = ownerSession) {
  const url = String(input);
  if (url.endsWith("/api/v1/session")) return response({ token: "local-token", session });
  if (url.endsWith("/api/v1/status")) return response(status);
  if (url.endsWith("/api/v1/settings")) return response(settings);
  if (url.endsWith("/api/v1/training-profile")) return response(profile);
  return response([]);
}

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("guided onboarding", () => {
  test("opens directly for a trusted LAN browser", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        return route(input, { ...ownerSession, id: "lan-session", role: "lan" as const });
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Configure this application" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Trusted LAN browser")).toBeInTheDocument();
  });

  test("starts with application setup before agent configuration", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => route(input)),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Configure this application" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Set up Engine + Pixi" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Create your first checkpoint" }),
    ).not.toBeInTheDocument();
  });

  test("one local setup button requests the composite controller job", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/status")) return response(status);
      if (url.endsWith("/api/v1/session"))
        return response({ token: "local-token", session: ownerSession });
      if (url.endsWith("/api/v1/settings")) return response(settings);
      if (url.endsWith("/api/v1/training-profile")) return response(profile);
      if (url.endsWith("/api/v1/jobs") && init?.method === "POST")
        return response(stackJob);
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const setupButton = await screen.findByRole("button", { name: "Set up Engine + Pixi" });
    await waitFor(() => expect(setupButton).toBeEnabled());
    fireEvent.click(setupButton);

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        kind: "dependency.stack.prepare",
      });
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).endsWith("/api/v1/training-profile"),
        ),
      ).toHaveLength(1);
    });
  });
});
