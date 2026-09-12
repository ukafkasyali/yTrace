import { useCallback, useEffect, useRef, useState } from 'react';

export function clampTime(time: number, duration: number): number {
  const end = Number.isFinite(duration) ? Math.max(0, duration) : 0;
  return Number.isFinite(time) ? Math.min(end, Math.max(0, time)) : 0;
}

export function advancePlayhead(start: number, elapsedMs: number, speed: number, duration: number): number {
  const elapsed = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0;
  const rate = Number.isFinite(speed) && speed > 0 ? speed : 0;
  return clampTime(clampTime(start, duration) + elapsed / 1000 * rate, duration);
}

/** Replay updates use elapsed time rather than frame counts, including elapsed time between delayed frames. */
export function useReplay(duration: number, initial = 8) {
  const end = Number.isFinite(duration) ? Math.max(0, duration) : 0;
  const [playhead, setPlayhead] = useState(() => clampTime(initial, end));
  const [playing, setPlaying] = useState(false);
  const [speed, updateSpeed] = useState(1);
  const position = useRef(playhead);
  const rate = useRef(speed);
  const lastFrame = useRef<number | null>(null);
  const running = useRef(false);

  const pause = useCallback(() => {
    running.current = false;
    lastFrame.current = null;
    setPlaying(false);
    setPlayhead(position.current);
  }, []);

  const seek = useCallback((time: number) => {
    running.current = false;
    lastFrame.current = null;
    position.current = clampTime(time, end);
    setPlayhead(position.current);
    setPlaying(false);
  }, [end]);

  const setSpeed = useCallback((next: number) => {
    if (!Number.isFinite(next) || next <= 0) return;
    rate.current = next;
    // Begin a fresh timing interval; never apply the new speed to old elapsed time.
    lastFrame.current = null;
    updateSpeed(next);
  }, []);

  const toggle = useCallback(() => {
    if (running.current) { pause(); return; }
    if (end <= 0 || position.current >= end || document.hidden) return;
    running.current = true;
    lastFrame.current = null;
    setPlaying(true);
  }, [end, pause]);

  const restart = useCallback(() => {
    position.current = 0;
    setPlayhead(0);
    lastFrame.current = null;
    running.current = end > 0 && !document.hidden;
    setPlaying(running.current);
  }, [end]);

  useEffect(() => {
    position.current = clampTime(position.current, end);
    setPlayhead(position.current);
    if (position.current >= end) pause();
  }, [end, pause]);

  useEffect(() => {
    const onVisibility = () => { if (document.hidden) pause(); };
    document.addEventListener('visibilitychange', onVisibility);
    if (document.hidden) pause();
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [pause]);

  useEffect(() => {
    if (!playing) return;
    let frameId = 0;
    let lastPublished = 0;
    let disposed = false;
    const tick = (now: number) => {
      if (disposed || !running.current) return;
      if (document.hidden) { pause(); return; }
      const elapsed = lastFrame.current === null ? 0 : Math.max(0, now - lastFrame.current);
      lastFrame.current = now;
      position.current = advancePlayhead(position.current, elapsed, rate.current, end);
      if (position.current >= end) {
        position.current = end;
        pause();
        return;
      }
      if (now - lastPublished >= 1000 / 30) {
        setPlayhead(position.current);
        lastPublished = now;
      }
      frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick);
    return () => {
      disposed = true;
      cancelAnimationFrame(frameId);
      lastFrame.current = null;
    };
  }, [playing, end, pause]);

  return { playhead: clampTime(playhead, end), playing, speed, setSpeed, toggle, pause, seek, restart };
}
