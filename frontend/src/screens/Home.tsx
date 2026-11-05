import { useEffect, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";

export function NewTripForm({ onCreated }: { onCreated?: () => void }) {
  const { refreshTrips, setTripId } = useApp();
  const [title, setTitle] = useState("나의 올레 여행");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [lodging, setLodging] = useState("");
  const [err, setErr] = useState("");
  const create = async () => {
    setErr("");
    try {
      const t = await api("/v1/trips", { body: { title, start_date: start, end_date: end, lodging_note: lodging || null } });
      await refreshTrips();
      setTripId(t.id);
      onCreated?.();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card">
      <h2>새 여행 만들기</h2>
      <label className="field">여행 이름<input type="text" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <div className="grid2">
        <label className="field">시작일<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="field">종료일<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
      </div>
      <label className="field">숙박 위치 메모(선택)<input type="text" value={lodging} onChange={(e) => setLodging(e.target.value)} placeholder="예: 서귀포 시내" /></label>
      {err && <p className="err">{err}</p>}
      <button className="btn" onClick={create} disabled={!start || !end}>만들기</button>
    </div>
  );
}

export default function Home() {
  const { trip, trips, setTripId, session, go } = useApp();
  const [today, setToday] = useState<Json | null>(null);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!trip) return;
    api(`/v1/trips/${trip.id}/today`).then(setToday).catch((e) => setErr(e.message));
  }, [trip]);

  if (!trip || creating) return <NewTripForm onCreated={() => setCreating(false)} />;

  const sched = today?.schedule;
  const notices = today?.notices;
  return (
    <div>
      {trips.length > 1 && (
        <select value={trip.id} onChange={(e) => setTripId(e.target.value)} aria-label="여행 선택">
          {trips.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
        </select>
      )}
      <h1>오늘의 브리핑</h1>
      {err && <p className="err">{err}</p>}
      {session && session.status !== "ended" && (
        <div className="banner">
          <strong>트레킹 진행 중</strong> · {session.route ?? "코스 미지정"} · {session.walked_km}km
          <div style={{ marginTop: 8 }}><button className="btn" onClick={() => go("walk")}>지도 보기</button></div>
        </div>
      )}
      <div className="card">
        <div className="spread"><h2>{today ? fmtDate(today.date) : "오늘"}</h2><span className="muted small">일몰 {today?.sunset ?? "–"}</span></div>
        {sched?.route ? (
          <div className="stack">
            <div className="metric">{sched.route.name}</div>
            <div>{sched.route.start_point} → {sched.route.end_point}</div>
            <div className="row">
              <span className="tag">{sched.route.distance_km}km</span>
              <span className="tag">난이도 {sched.route.difficulty}</span>
              <span className="tag">{sched.planned_start}–{sched.planned_end}</span>
            </div>
            {today?.estimate && <div className="muted small">예상 소요 약 {Math.round(today!.estimate.duration_min / 6) / 10}시간 ({today!.estimate.assumptions})</div>}
            {today?.route?.freshness?.data_status !== "verified" && <span className="tag warn">코스 정보 최신 확인 필요</span>}
          </div>
        ) : sched ? <p>{sched.note ?? "오늘은 걷는 일정이 없어요."}</p> : (
          <p className="muted">오늘 승인된 일정이 없습니다. <button className="btn ghost" onClick={() => go("plan")}>일정 만들기</button></p>
        )}
      </div>

      {notices && (
        <div className={`banner ${notices.notices.length ? "safety" : "warn"}`}>
          <strong>공식 공지</strong>{" "}
          {notices.notices.length ? `${notices.notices.length}건` : "등록된 공지 없음"}
          {notices.notices.map((n: Json) => <div key={n.id}>· <a href={n.source_url} target="_blank" rel="noopener">{n.title}</a></div>)}
          {notices.caveat && <div className="small">{notices.caveat}</div>}
          <div className="small"><a href={notices.official_url} target="_blank" rel="noopener">제주올레 공식 사이트에서 확인</a></div>
        </div>
      )}

      {sched?.checklist?.length > 0 && (
        <div className="card">
          <h2>출발 전 확인</h2>
          <ul>{sched.checklist.map((c: string) => <li key={c}>{c}</li>)}</ul>
          <p className="muted small">날씨는 동행자에게 물어보면 기상청 예보를 출처·발표 시각과 함께 알려드려요.</p>
        </div>
      )}

      <div className="btn-row">
        <button className="btn big" onClick={() => go("walk")}>트레킹 시작</button>
        <button className="btn big secondary" onClick={() => go("ai")}>AI에게 질문</button>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn ghost" onClick={() => go("journal")}>오늘 기록하기</button>
        <button className="btn ghost" onClick={() => setCreating(true)}>새 여행</button>
      </div>
    </div>
  );
}
