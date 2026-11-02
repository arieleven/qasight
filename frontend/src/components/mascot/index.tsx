import { PixelArt } from "./PixelArt";
import { AnimatedPixelArt } from "./AnimatedPixelArt";
import { FLEX, GLOVES, HURT, SMASH } from "./animations";

export { PixelArt } from "./PixelArt";
export { EyeLogo, EYE_BRAND_BLUE } from "./EyeLogo";
export { AnimatedPixelArt } from "./AnimatedPixelArt";
export { FLEX, SMASH, HURT, GLOVES, ANIMATIONS, MASCOT_PALETTE } from "./animations";

export interface MascotProps {
  size?: number;
  animate?: boolean;
  className?: string;
}

// Open palm turned up with a floating question mark — empty states.
const SHRUG_GRID = [
  "................",
  "..........ggg...",
  ".........g...g..",
  ".............g..",
  "............g...",
  "...........g....",
  "................",
  "...........g....",
  "................",
  ".s.s.s..........",
  ".osssso.........",
  ".osssoo.........",
  ".ossssoo........",
  "..osssssoooooo..",
  "...ossssssssso..",
  "....oooooooo....",
];

/** Bicep pump — the "run passed" celebration. */
export function MuscleFlex({ size = 96, animate = false, className }: MascotProps) {
  return (
    <AnimatedPixelArt
      animation={FLEX}
      playing={animate}
      stillFrame={2}
      size={size}
      className={className}
      title="Flexing muscle — success"
    />
  );
}

/** Arm takes a hit and droops with a bandage — the "run failed" reaction. */
export function MuscleDefeated({ size = 96, animate = false, className }: MascotProps) {
  return (
    <AnimatedPixelArt
      animation={HURT}
      playing={animate}
      stillFrame={3}
      size={size}
      className={className}
      title="Hurt muscle — failure"
    />
  );
}

/** Fist smashing through bricks — heavy work in progress. */
export function MuscleSmash({ size = 96, animate = true, className }: MascotProps) {
  return (
    <AnimatedPixelArt
      animation={SMASH}
      playing={animate}
      stillFrame={0}
      size={size}
      className={className}
      title="Smashing bricks — running"
    />
  );
}

/** Red and blue gloves trading a punch — comparisons and deltas. */
export function GlovesPunch({ size = 96, animate = true, className }: MascotProps) {
  return (
    <AnimatedPixelArt
      animation={GLOVES}
      playing={animate}
      stillFrame={2}
      size={size}
      className={className}
      title="Gloves punching — versus"
    />
  );
}

/** Shrugging palm with a question mark — nothing here yet. */
export function MuscleShrug({ size = 96, className }: MascotProps) {
  return <PixelArt grid={SHRUG_GRID} size={size} className={className} title="Shrugging muscle — nothing here yet" />;
}
