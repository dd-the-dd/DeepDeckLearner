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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("guided onboarding", () => {
  test("starts with outcomes instead of an embedded training form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
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
        return response({ token: "local-token" });
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

  test("requires an explicit deck pool and never substitutes smoke training", async () => {
    const trainingStatus: CapabilityStatus = {
      ...status,
      engine: {
        ...status.engine,
        source_available: true,
        synced: true,
        built: true,
        healthy: true,
      },
      workflows: { training_decks: false },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/status")) return response(trainingStatus);
      if (url.includes("/api/v1/catalog/local-decks"))
        return response([
          { deckSessionId: "deck-1", deckName: "Legacy Reanimator" },
          { deckSessionId: "deck-2", deckName: "Death and Taxes" },
        ]);
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: /Train an agent/i }),
    );

    const launch = await screen.findByRole("button", {
      name: /Train V12 on selected decks/i,
    });
    expect(launch).toBeDisabled();
    expect(screen.getByText(/No deck selected/i)).toBeInTheDocument();

    fireEvent.click(
      await screen.findByRole("button", { name: /Legacy Reanimator/i }),
    );
    expect(screen.getByText("1 deck selected")).toBeInTheDocument();
    expect(launch).toBeDisabled();
    expect(
      screen.getByText(/will not replace your chosen decks with sample data/i),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input, init]) =>
        String(input).endsWith("/api/v1/jobs") && init?.method === "POST",
      ),
    ).toBe(false);
  });
});
