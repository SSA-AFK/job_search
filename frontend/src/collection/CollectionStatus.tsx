import { RotateCw } from "lucide-react";
import { useEffect, useReducer, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { CollectionRequest } from "../api/types";
import {
  collectionReducer,
  type CollectionSession,
  initialCollectionState,
  nextPollDelay,
  POLL_TIMEOUT_MS,
  publicCollectionError,
  shouldPoll,
} from "./polling";

function waitForPoll(delay: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, delay);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Collection polling was aborted", "AbortError"));
    }, { once: true });
  });
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export function CollectionStatus({
  query,
  sessions,
}: {
  query: string;
  sessions: Map<string, CollectionSession>;
}) {
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(collectionReducer, initialCollectionState);
  const [refreshVersion, setRefreshVersion] = useState(0);

  useEffect(() => {
    const normalizedQuery = query.trim();
    const controller = new AbortController();
    let active = true;
    dispatch({ type: "submitting" });

    const update = (request: CollectionRequest) => {
      if (active) dispatch({ type: "received", request });
      if (request.status === "succeeded" && request.company_id && active) {
        navigate(`/companies/${request.company_id}`);
      }
    };

    const collect = async () => {
      let session = sessions.get(normalizedQuery);
      if (!session) {
        session = { request: null, submission: null };
        sessions.set(normalizedQuery, session);
      }

      let request: CollectionRequest;
      if (session.request) {
        request = await api.getCollectionRequest(session.request.id, controller.signal);
      } else {
        if (!session.submission) {
          session.submission = api.createCollectionRequest(normalizedQuery, controller.signal);
        }
        try {
          request = await session.submission;
        } catch (error: unknown) {
          if (!isAbortError(error)) throw error;
          session.submission = api.createCollectionRequest(normalizedQuery, controller.signal);
          request = await session.submission;
        }
        session.submission = null;
        session.request = request;
      }

      update(request);
      if (!shouldPoll(request.status)) return;

      const deadline = Date.now() + POLL_TIMEOUT_MS;
      let attempt = 0;
      while (shouldPoll(request.status)) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) {
          if (active) dispatch({ type: "timed_out", request });
          return;
        }
        await waitForPoll(Math.min(nextPollDelay(attempt), remaining), controller.signal);
        if (deadline - Date.now() <= 0) {
          if (active) dispatch({ type: "timed_out", request });
          return;
        }
        request = await api.getCollectionRequest(request.id, controller.signal);
        session.request = request;
        update(request);
        attempt += 1;
      }
    };

    void collect().catch((error: unknown) => {
      if (!active || isAbortError(error)) return;
      dispatch({ type: "request_failed", errorCode: error instanceof ApiError ? error.code ?? null : null });
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [navigate, query, refreshVersion]);

  const content = state.status === "submitting"
    ? ["正在提交采集请求", "正在确认是否可以补充资料"]
    : state.status === "queued"
      ? ["正在排队", "已安排资料采集，完成后会自动更新结果。"]
      : state.status === "running"
        ? ["正在采集", "正在整理公开资料，完成后会自动更新结果。"]
        : state.status === "partial"
          ? ["已完成部分资料采集", "部分公开资料已更新，可以稍后再次搜索。"]
          : state.status === "succeeded"
            ? ["采集完成", "正在打开公司资料。"]
            : state.status === "timed_out"
              ? ["采集仍在进行中", "状态自动更新已暂停，请按需刷新查看最新结果。"]
              : state.status === "failed"
                ? ["采集未完成", publicCollectionError(state.errorCode)]
                : ["暂未收录这家公司", "正在确认是否可以补充资料"];

  return (
    <div className="collection-status" role="status" aria-live="polite" aria-atomic="true">
      <div>
        <h2>{content[0]}</h2>
        <p>{content[1]}</p>
      </div>
      {state.status === "timed_out" ? (
        <button className="secondary-button" type="button" onClick={() => setRefreshVersion((version) => version + 1)}>
          <RotateCw aria-hidden="true" size={16} />
          刷新状态
        </button>
      ) : null}
    </div>
  );
}
