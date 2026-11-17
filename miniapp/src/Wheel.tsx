// Колесо фортуны на SVG. Результат приходит с сервера, колесо лишь докручивается до него.

import { forwardRef, useImperativeHandle, useRef } from "react";
import type { Sector } from "./api";
import { haptic } from "./telegram";

const COLORS = ["#7c5cff", "#ff5c8a", "#ffb03b", "#2ec4b6", "#4d8cff", "#ff7a45", "#a35cff", "#22c55e"];
const EMPTY_COLOR = "#5b6475";
const RADIUS = 100;
const SPIN_MS = 5200;
const EXTRA_TURNS = 6;

export interface WheelHandle {
  /** Докрутить колесо до сектора; промис завершается, когда колесо остановилось. */
  spinTo(sectorId: number): Promise<void>;
}

const point = (angleDeg: number, r: number) => {
  const a = (angleDeg * Math.PI) / 180;
  return [r * Math.sin(a), -r * Math.cos(a)] as const; // 0° — вверх, по часовой стрелке
};

function sectorPath(center: number, step: number): string {
  const [x1, y1] = point(center - step / 2, RADIUS);
  const [x2, y2] = point(center + step / 2, RADIUS);
  const large = step > 180 ? 1 : 0;
  return `M0 0 L${x1} ${y1} A${RADIUS} ${RADIUS} 0 ${large} 1 ${x2} ${y2} Z`;
}

/** Делит название на строки до ~11 символов, максимум 3 строки. */
function wrap(title: string, max = 11): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of title.split(" ")) {
    if (line && (line + " " + word).length > max) {
      lines.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  if (line) lines.push(line);
  return lines.length > 3 ? [...lines.slice(0, 2), lines.slice(2).join(" ").slice(0, max - 1) + "…"] : lines;
}

// Плавное замедление к концу (как у настоящего колеса).
const easeOut = (t: number) => 1 - Math.pow(1 - t, 4);

export const Wheel = forwardRef<WheelHandle, { sectors: Sector[] }>(function Wheel({ sectors }, ref) {
  const groupRef = useRef<SVGGElement>(null);
  const rotation = useRef(0);
  const step = 360 / Math.max(sectors.length, 1);

  const sectorUnderPointer = (rot: number) => {
    const angle = (((-rot % 360) + 360) % 360 + step / 2) % 360;
    return Math.floor(angle / step);
  };

  useImperativeHandle(ref, () => ({
    spinTo(sectorId: number) {
      const index = sectors.findIndex((s) => s.id === sectorId);
      const target = index < 0 ? 0 : index;
      // Небольшое случайное смещение внутри сектора — только для вида, приз уже выбран сервером.
      const jitter = (Math.random() - 0.5) * step * 0.6;
      const start = rotation.current;
      const desired = (((-(target * step + jitter)) % 360) + 360) % 360;
      const current = ((start % 360) + 360) % 360;
      const end = start + EXTRA_TURNS * 360 + ((desired - current + 360) % 360);

      return new Promise<void>((resolve) => {
        const t0 = performance.now();
        let lastSector = sectorUnderPointer(start);
        const frame = (now: number) => {
          const t = Math.min(1, (now - t0) / SPIN_MS);
          const rot = start + (end - start) * easeOut(t);
          rotation.current = rot;
          groupRef.current?.setAttribute("transform", `rotate(${rot})`);
          const s = sectorUnderPointer(rot);
          if (s !== lastSector) {
            lastSector = s;
            haptic.tick();
          }
          if (t < 1) requestAnimationFrame(frame);
          else resolve();
        };
        requestAnimationFrame(frame);
      });
    },
  }));

  let colorIndex = 0;
  return (
    <svg className="wheel" viewBox="-112 -118 224 230" role="img" aria-label="Колесо фортуны">
      <defs>
        <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="4" stdDeviation="5" floodOpacity="0.35" />
        </filter>
      </defs>
      <circle r={RADIUS + 8} className="wheel-rim" filter="url(#shadow)" />
      <g ref={groupRef}>
        {sectors.map((s, i) => {
          const center = i * step;
          const fill = s.is_empty ? EMPTY_COLOR : COLORS[colorIndex++ % COLORS.length];
          const lines = wrap(s.title);
          return (
            <g key={s.id}>
              <path d={sectorPath(center, step)} fill={fill} stroke="rgba(255,255,255,0.85)" strokeWidth="1.2" />
              <g transform={`rotate(${center})`}>
                <text y={-78} className="wheel-emoji">{s.emoji ?? "🎁"}</text>
                <text y={-58} className="wheel-label">
                  {lines.map((l, k) => (
                    <tspan key={k} x={0} dy={k === 0 ? 0 : 8.5}>{l}</tspan>
                  ))}
                </text>
              </g>
            </g>
          );
        })}
      </g>
      {Array.from({ length: sectors.length }, (_, i) => {
        const [x, y] = point(i * step + step / 2, RADIUS + 4);
        return <circle key={i} cx={x} cy={y} r={2} className="wheel-bulb" />;
      })}
      <circle r={16} className="wheel-hub" />
      <text y={5} className="wheel-hub-text">GG</text>
      <path d="M-10 -116 L10 -116 L0 -94 Z" className="wheel-pointer" />
    </svg>
  );
});
