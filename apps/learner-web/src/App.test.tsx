import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import App from "./App";
import type { CapabilityStatus, Job } from "./api";

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

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("guided onboarding", () => {
  test("asks a LAN browser for the host pairing code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "Pair this LAN device to continue." }), {
            status: 401,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Pair this device" })).toBeInTheDocument();
    expect(screen.getByLabelText("Pairing code")).toBeInTheDocument();
  });

  test("starts with outcomes instead of an embedded training form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/session"))
          return response({ token: "local-token", session: ownerSession });
        return url.endsWith("/api/v1/status") ? response(status) : response([]);
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "What do you want to do?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Train an agent/i }),
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
      if (url.endsWith("/api/v1/jobs") && init?.method === "POST")
        return response(stackJob);
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: /Test an agent locally/i }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Set up Engine + Pixi" }),
    );

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        kind: "dependency.stack.prepare",
      });
    });
  });
});
