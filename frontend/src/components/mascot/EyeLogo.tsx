// Brand mark: a flat open eye. The iris colour is set by the caller (the
// health page tints it with the project's health score), the eye blinks on
// hover and the pupil spins while the app is loading. Motion is off for
// people who ask for reduced motion (see styles.css).

export interface EyeLogoProps {
  size?: number;
  /** Iris colour; defaults to the brand blue. */
  irisColor?: string;
  /** Spin the pupil, e.g. while data is loading. */
  spinning?: boolean;
  className?: string;
  title?: string;
}

export const EYE_BRAND_BLUE = "#0075ff";

// 16 short streaks around the pupil give the iris its texture.
const STREAKS = Array.from({ length: 16 }, (_, i) => {
  const angle = (i * Math.PI * 2) / 16;
  const [cos, sin] = [Math.cos(angle), Math.sin(angle)];
  return { x1: 50 + cos * 11.5, y1: 30 + sin * 11.5, x2: 50 + cos * 16.5, y2: 30 + sin * 16.5 };
});

export function EyeLogo({ size = 26, irisColor = EYE_BRAND_BLUE, spinning = false, className, title = "Testo" }: EyeLogoProps) {
  return (
    <svg
      viewBox="0 0 100 60"
      width={size}
      height={Math.round((size * 60) / 100)}
      role="img"
      aria-label={title}
      className={`eye-logo ${className ?? ""}`.trim()}
      data-spinning={spinning ? "true" : "false"}
      data-testid="eye-logo"
    >
      <title>{title}</title>
      <g className="eye-logo__ball">
        <path d="M3 30 Q50 -10 97 30 Q50 70 3 30 Z" fill="#f5f7fb" />
        <circle cx="50" cy="30" r="20" fill={irisColor} data-testid="eye-logo-iris" style={{ transition: "fill 300ms ease" }} />
        <g stroke="#ffffff" strokeOpacity="0.32" strokeWidth="2.6" strokeLinecap="round">
          {STREAKS.map((s, i) => (
            <line key={i} {...s} />
          ))}
        </g>
        <g className="eye-logo__pupil">
          <circle cx="50" cy="30" r="9" fill="#0b1026" />
          <circle cx="53.6" cy="26.4" r="2.6" fill="#ffffff" />
        </g>
      </g>
    </svg>
  );
}
