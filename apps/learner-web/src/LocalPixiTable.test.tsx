import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import LocalPixiTable, { latestEngineView } from "./LocalPixiTable";

vi.mock("./LocalPixiRenderer", () => ({
  default: ({ matchup, onAction, view: currentView, controlledPlayerId, onHideHand }: {
    matchup: string;
    onAction: (actionId: string) => void;
    view: typeof view;
    controlledPlayerId?: string;
    onHideHand?: () => void;
  }) => <div data-testid="pixi-renderer">
    <span>{matchup}</span>
    <pre data-testid="seat-view">{JSON.stringify(currentView.state)}</pre>
    <span data-testid="controlled-seat">{controlledPlayerId}</span>
    {onHideHand && <button onClick={onHideHand}>Hide my hand</button>}
    {(currentView.decision?.options ?? []).map((action) => (
      <button key={action.id} type="button" onClick={() => onAction(action.id)}>
        {action.label}
      </button>
    ))}
  </div>,
}));

const view = {
  sessionId: "game-session:8",
  revision: 1,
  state: {
    status: "inProgress",
    turnNumber: 1,
    activePlayer: 0,
    step: "untap",
    players: [
      { id: "local-human", name: "Player deck", life: 20, hand: Array(7), library: Array(53) },
      { id: "local-agent", name: "Agent deck", life: 20, hand: Array(7), library: Array(53) },
    ],
    outcome: null,
  },
  decision: {
    id: "mulligan:local-human:0",
    kind: "mulligan",
    playerId: "local-human",
    options: [
      { id: "keep:local-human:0", kind: "keepHand", label: "Keep opening hand" },
      { id: "mulligan:local-human:0", kind: "takeMulligan", label: "Take a mulligan" },
    ],
  },
};

function response(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('leaving waits for controller replay finalization and never deletes the Engine session first', async () => {
  const fetchMock = vi.fn<typeof fetch>(() => response(view)); vi.stubGlobal('fetch', fetchMock);
  let finish!: () => void;
  const close = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  render(<LocalPixiTable engineUrl="http://127.0.0.1:8897" sessionId={view.sessionId} matchup="Test" onClose={close} />);
  await screen.findByTestId('pixi-renderer');
  fireEvent.click(screen.getByRole('button', { name: 'Back to lab' }));
  expect(screen.getByRole('button', { name: 'Saving replay & closing…' })).toBeDisabled();
  expect(close).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  await act(async () => { finish(); });
});

test('a failed close remains visible and can be retried without deleting the session', async () => {
  vi.stubGlobal('fetch', vi.fn(() => response(view)));
  const close = vi.fn().mockRejectedValueOnce(new Error('The controller disconnected.')).mockResolvedValue(undefined);
  render(<LocalPixiTable engineUrl="http://127.0.0.1:8897" sessionId={view.sessionId} matchup="Test" onClose={close} />);
  await screen.findByTestId('pixi-renderer');
  fireEvent.click(screen.getByRole('button', { name: 'Back to lab' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('controller disconnected');
  fireEvent.click(screen.getByRole('button', { name: 'Back to lab' }));
  await waitFor(() => expect(close).toHaveBeenCalledTimes(2));
});

describe("local behavior test", () => {
  test("never rolls the table back when an older poll finishes late", () => {
    const latest = { ...view, revision: 12 };
    const stalePoll = { ...view, revision: 11 };

    expect(latestEngineView(latest, stalePoll)).toBe(latest);
    expect(latestEngineView(stalePoll, latest)).toBe(latest);
  });

  test("keeps Engine decisions usable independently from the Pixi renderer", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (String(_input).includes("/presentation")) {
        return response({ versionId: "deck-one", name: "Player deck", cards: [] });
      }
      if (init?.method === "POST") return response({ ...view, revision: 2, decision: null });
      return response(view);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <LocalPixiTable
        deckVersionIds={["deck-one", "deck-two"]}
        engineUrl="http://127.0.0.1:8787"
        sessionId="game-session:8"
        matchup="Player deck vs Agent deck"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText("Player deck vs Agent deck")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/catalog/decks/deck-one/presentation",
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/catalog/decks/deck-two/presentation",
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Keep opening hand" }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(request).toBeDefined();
      expect(String(request?.[0])).toBe(
        "http://127.0.0.1:8787/game/sessions/game-session:8/actions",
      );
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        actionId: "keep:local-human:0",
        decisionId: "mulligan:local-human:0",
        revision: 1,
      });
    });
  });
});

test('two humans explicitly hand over, remount the viewer, and never render the other private hand',async()=>{
  const privateCard=(id:string)=>({instanceId:id,definition:{name:`Private ${id}`},flags:{knownToViewer:true}});
  let latest={...view,state:{...view.state,players:[
    {id:'local-human',name:'Player 1',life:20,hand:[privateCard('one')],library:[privateCard('library-one')]},
    {id:'local-human-2',name:'Player 2',life:20,hand:[privateCard('two')],library:[privateCard('library-two')]},
  ]}};
  const fetchMock=vi.fn((_input:RequestInfo|URL,init?:RequestInit)=>{
    if(init?.method==='POST')latest={...latest,revision:2,decision:{...view.decision,id:'mulligan:two',playerId:'local-human-2'}};
    return response(latest);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<LocalPixiTable engineUrl="http://127.0.0.1:8897" sessionId={view.sessionId} humanPlayerIds={['local-human','local-human-2']} matchup="Shared-screen match" onClose={vi.fn()}/>);
  await screen.findByRole('region',{name:'Pass the screen'});
  expect(screen.queryByTestId('pixi-renderer')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/I’m ready/}));
  expect(await screen.findByTestId('seat-view')).toHaveTextContent('Private one');
  expect(screen.getByTestId('seat-view')).not.toHaveTextContent('Private two');
  expect(screen.getByTestId('seat-view')).not.toHaveTextContent('Private library');
  expect(screen.getByTestId('controlled-seat')).toHaveTextContent('local-human');
  fireEvent.click(screen.getByRole('button',{name:'Keep opening hand'}));
  await screen.findByRole('heading',{name:'Pass the screen to Player 2'});
  expect(screen.queryByTestId('pixi-renderer')).toBeNull();
  expect(document.body.textContent).not.toContain('Private one');
  fireEvent.click(screen.getByRole('button',{name:/I’m ready/}));
  expect(await screen.findByTestId('seat-view')).toHaveTextContent('Private two');
  expect(screen.getByTestId('seat-view')).not.toHaveTextContent('Private one');
  expect(screen.getByTestId('controlled-seat')).toHaveTextContent('local-human-2');
  fireEvent.click(screen.getByRole('button',{name:'Hide my hand'}));
  expect(screen.getByRole('region',{name:'Pass the screen'})).toBeInTheDocument();
  expect(screen.queryByTestId('pixi-renderer')).toBeNull();
  expect(fetchMock.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
});

test('a connection failure hides the hand and requires a new ready gesture after reconnecting',async()=>{
  let offline=false;
  const snapshot={...view,state:{...view.state,players:[...view.state.players,{id:'local-human-2',name:'Player 2',life:20,hand:[],library:[]}]}};
  vi.stubGlobal('fetch',vi.fn(()=>offline?Promise.reject(new Error('PRIVATE_OTHER_HAND_DETAIL')):response(snapshot)));
  render(<LocalPixiTable engineUrl="http://127.0.0.1:8897" sessionId={view.sessionId} humanPlayerIds={['local-human','local-human-2']} matchup="Shared-screen match" onClose={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:/I’m ready/}));
  await screen.findByTestId('pixi-renderer');
  offline=true;
  await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('Private hands are hidden'),{timeout:2500});
  expect(screen.queryByTestId('pixi-renderer')).toBeNull();
  expect(document.body.textContent).not.toContain('PRIVATE_OTHER_HAND_DETAIL');
  expect(screen.getByRole('button',{name:/I’m ready/})).toBeDisabled();
  offline=false;
  await waitFor(()=>expect(screen.getByRole('button',{name:/I’m ready/})).toBeEnabled(),{timeout:2500});
  expect(screen.queryByTestId('pixi-renderer')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/I’m ready/}));
  await screen.findByTestId('pixi-renderer');
});
