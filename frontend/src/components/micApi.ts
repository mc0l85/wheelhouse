// Adapter from the REST client to the post-dictation sequence's api.
import { api } from "../api/client";
import type { MicSubmitApi } from "./micSubmit";

export function makeMicApi(toast: MicSubmitApi["toast"]): MicSubmitApi {
  return {
    deliver: async (paneId, text) => { await api.sendText(paneId, text); },
    sendEnter: async (paneId) => { await api.sendKeys(paneId, ["Enter"]); },
    status: async (paneId) => {
      const p = (await api.panes()).panes.find((x) => x.pane_id === paneId);
      return p ? (p.agent_status ?? "unknown") : null;
    },
    toast,
  };
}
