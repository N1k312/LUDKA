// Конфетти при выигрыше: лёгкая анимация на canvas без библиотек.
// Не показывается, если в системе включено «уменьшить движение».

import { useEffect, useRef } from "react";
import { reducedMotion } from "./telegram";

export function Confetti({ colors }: { colors: string[] }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || reducedMotion()) return;
    const w = (canvas.width = canvas.offsetWidth * devicePixelRatio);
    const h = (canvas.height = canvas.offsetHeight * devicePixelRatio);
    const pieces = Array.from({ length: 140 }, () => ({
      x: w / 2 + (Math.random() - 0.5) * w * 0.2,
      y: h * 0.35,
      vx: (Math.random() - 0.5) * 22 * devicePixelRatio,
      vy: (-Math.random() * 18 - 6) * devicePixelRatio,
      size: (5 + Math.random() * 6) * devicePixelRatio,
      spin: Math.random() * Math.PI,
      color: colors[Math.floor(Math.random() * colors.length)],
    }));
    const start = performance.now();
    let frame = 0;
    const draw = (now: number) => {
      const t = (now - start) / 1000;
      ctx.clearRect(0, 0, w, h);
      ctx.globalAlpha = Math.max(0, 1 - t / 2.2);
      for (const p of pieces) {
        p.vy += 0.6 * devicePixelRatio;
        p.vx *= 0.99;
        p.x += p.vx;
        p.y += p.vy;
        p.spin += 0.2;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.spin);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        ctx.restore();
      }
      if (t < 2.2) frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [colors]);

  return <canvas ref={ref} className="confetti" aria-hidden="true" />;
}
