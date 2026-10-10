/**
 * Aevum-3D: Environment Theme Registry
 * Configures lighting, fog, materials, and decorative props for each architectural theme
 */

const THREE = window.THREE;

export const THEMES = {
  industrial: {
    name: 'Industrial Citadel',
    icon: '🏭',
    bg: 0x0f172a,
    fogColor: 0x0f172a,
    fogDensity: 0.015,
    ambientColor: 0xdbeafe,
    ambientIntensity: 0.7,
    sunColor: 0xffffff,
    sunIntensity: 0.9,
    accentLight1: { color: 0x38bdf8, intensity: 1.2, pos: [-10, 8, -5] },
    accentLight2: { color: 0xa855f7, intensity: 1.2, pos: [12, 4, 10] },
    materials: {
      l4Floor: { color: 0x1e293b, roughness: 0.8, metalness: 0.2 },
      l3Floor: { color: 0x334155, roughness: 0.7, metalness: 0.3 },
      l2Floor: { color: 0x475569, roughness: 0.6, metalness: 0.4 },
      l1Floor: { color: 0x64748b, roughness: 0.5, metalness: 0.5 },
      railing: { color: 0x93c5fd, roughness: 0.1, transmission: 0.8, transparent: true, opacity: 0.5 },
      canopy: { color: 0x1e3a8a, roughness: 0.2, metalness: 0.8 },
      hazard: { color: 0xfacc15, roughness: 0.4 },
      gridColor1: 0x334155,
      gridColor2: 0x1e293b
    }
  },

  greenhouse: {
    name: 'Lush Greenhouse',
    icon: '🌿',
    bg: 0x064e3b,
    fogColor: 0x064e3b,
    fogDensity: 0.012,
    ambientColor: 0xdcfce7,
    ambientIntensity: 0.9,
    sunColor: 0xfef08a,
    sunIntensity: 1.1,
    accentLight1: { color: 0x34d399, intensity: 1.0, pos: [-10, 8, -5] },
    accentLight2: { color: 0xfde047, intensity: 0.9, pos: [12, 4, 10] },
    materials: {
      l4Floor: { color: 0x14532d, roughness: 0.9, metalness: 0.0 }, // Grass
      l3Floor: { color: 0x78350f, roughness: 0.8, metalness: 0.1 }, // Terracotta
      l2Floor: { color: 0x92400e, roughness: 0.7, metalness: 0.1 },
      l1Floor: { color: 0x15803d, roughness: 0.8, metalness: 0.0 },
      railing: { color: 0x86efac, roughness: 0.1, transmission: 0.9, transparent: true, opacity: 0.4 },
      canopy: { color: 0x047857, roughness: 0.3, metalness: 0.2 },
      hazard: { color: 0x16a34a, roughness: 0.5 },
      gridColor1: 0x166534,
      gridColor2: 0x14532d
    }
  },

  studio: {
    name: 'Cozy Studio',
    icon: '🪵',
    bg: 0x271911,
    fogColor: 0x271911,
    fogDensity: 0.014,
    ambientColor: 0xfef3c7,
    ambientIntensity: 0.8,
    sunColor: 0xfde68a,
    sunIntensity: 0.85,
    accentLight1: { color: 0xf59e0b, intensity: 1.3, pos: [-10, 8, -5] },
    accentLight2: { color: 0xd97706, intensity: 1.1, pos: [12, 4, 10] },
    materials: {
      l4Floor: { color: 0x451a03, roughness: 0.6, metalness: 0.1 }, // Hardwood
      l3Floor: { color: 0x78350f, roughness: 0.5, metalness: 0.1 },
      l2Floor: { color: 0x9a3412, roughness: 0.5, metalness: 0.1 },
      l1Floor: { color: 0x3f2e23, roughness: 0.6, metalness: 0.1 },
      railing: { color: 0xd97706, roughness: 0.3, metalness: 0.7 }, // Brass/copper railing
      canopy: { color: 0xb45309, roughness: 0.4, metalness: 0.5 },
      hazard: { color: 0xd97706, roughness: 0.5 },
      gridColor1: 0x5c2b0e,
      gridColor2: 0x3d1a08
    }
  },

  haunted: {
    name: 'Haunted Mansion',
    icon: '🏰',
    bg: 0x090514,
    fogColor: 0x090514,
    fogDensity: 0.022,
    ambientColor: 0x6b21a8,
    ambientIntensity: 0.5,
    sunColor: 0x38bdf8,
    sunIntensity: 0.5,
    accentLight1: { color: 0x10b981, intensity: 1.6, pos: [-10, 8, -5] }, // Ghostly green
    accentLight2: { color: 0xc084fc, intensity: 1.5, pos: [12, 4, 10] }, // Eerie violet
    materials: {
      l4Floor: { color: 0x18181b, roughness: 0.9, metalness: 0.2 }, // Dark cobblestone
      l3Floor: { color: 0x27272a, roughness: 0.8, metalness: 0.3 },
      l2Floor: { color: 0x3f3f46, roughness: 0.8, metalness: 0.3 },
      l1Floor: { color: 0x1e1b4b, roughness: 0.7, metalness: 0.4 },
      railing: { color: 0x059669, roughness: 0.2, transmission: 0.6, transparent: true, opacity: 0.6 },
      canopy: { color: 0x4c1d95, roughness: 0.5, metalness: 0.6 },
      hazard: { color: 0x7c3aed, roughness: 0.6 },
      gridColor1: 0x3b0764,
      gridColor2: 0x18181b
    }
  },

  winter: {
    name: 'Winter Frost',
    icon: '❄️',
    bg: 0x0f172a,
    fogColor: 0xcfdbe6,
    fogDensity: 0.016,
    ambientColor: 0xe0f2fe,
    ambientIntensity: 1.0,
    sunColor: 0xffffff,
    sunIntensity: 1.1,
    accentLight1: { color: 0x38bdf8, intensity: 1.1, pos: [-10, 8, -5] },
    accentLight2: { color: 0x93c5fd, intensity: 1.0, pos: [12, 4, 10] },
    materials: {
      l4Floor: { color: 0xe2e8f0, roughness: 0.4, metalness: 0.1 }, // Snow/ice
      l3Floor: { color: 0xf1f5f9, roughness: 0.5, metalness: 0.1 },
      l2Floor: { color: 0x94a3b8, roughness: 0.4, metalness: 0.2 },
      l1Floor: { color: 0xcfdbe6, roughness: 0.3, metalness: 0.2 },
      railing: { color: 0xbae6fd, roughness: 0.05, transmission: 0.95, transparent: true, opacity: 0.7 },
      canopy: { color: 0x0284c7, roughness: 0.2, metalness: 0.5 },
      hazard: { color: 0x38bdf8, roughness: 0.4 },
      gridColor1: 0x94a3b8,
      gridColor2: 0xcfdbe6
    }
  },

  beach: {
    name: 'Tropical Beach',
    icon: '🏖️',
    bg: 0x0284c7,
    fogColor: 0x0284c7,
    fogDensity: 0.010,
    ambientColor: 0xfef3c7,
    ambientIntensity: 0.9,
    sunColor: 0xfbbf24, // Golden hour
    sunIntensity: 1.2,
    accentLight1: { color: 0x06b6d4, intensity: 1.2, pos: [-10, 8, -5] },
    accentLight2: { color: 0xf97316, intensity: 1.1, pos: [12, 4, 10] },
    materials: {
      l4Floor: { color: 0xfef08a, roughness: 0.95, metalness: 0.0 }, // Sand
      l3Floor: { color: 0xb45309, roughness: 0.7, metalness: 0.1 }, // Pier deck
      l2Floor: { color: 0xd97706, roughness: 0.6, metalness: 0.1 },
      l1Floor: { color: 0xfde047, roughness: 0.8, metalness: 0.0 },
      railing: { color: 0xfde68a, roughness: 0.6, metalness: 0.2 }, // Ropes / wood
      canopy: { color: 0x0d9488, roughness: 0.3, metalness: 0.3 }, // Tiki / cabana teal
      hazard: { color: 0xf97316, roughness: 0.5 },
      gridColor1: 0xf59e0b,
      gridColor2: 0xfef08a
    }
  },

  cyberpunk: {
    name: 'Cyberpunk City',
    icon: '🌆',
    bg: 0x050508,
    fogColor: 0x050508,
    fogDensity: 0.018,
    ambientColor: 0x1e1b4b,
    ambientIntensity: 0.6,
    sunColor: 0xf43f5e,
    sunIntensity: 0.7,
    accentLight1: { color: 0x06b6d4, intensity: 2.2, pos: [-10, 8, -5] }, // Cyan neon
    accentLight2: { color: 0xf43f5e, intensity: 2.2, pos: [12, 4, 10] }, // Magenta neon
    materials: {
      l4Floor: { color: 0x09090b, roughness: 0.2, metalness: 0.8 }, // Wet dark asphalt
      l3Floor: { color: 0x18181b, roughness: 0.3, metalness: 0.7 },
      l2Floor: { color: 0x27272a, roughness: 0.4, metalness: 0.6 },
      l1Floor: { color: 0x09090b, roughness: 0.2, metalness: 0.9 },
      railing: { color: 0x06b6d4, roughness: 0.1, transmission: 0.7, transparent: true, opacity: 0.8 },
      canopy: { color: 0xf43f5e, roughness: 0.2, metalness: 0.8 },
      hazard: { color: 0xec4899, roughness: 0.3 },
      gridColor1: 0x06b6d4,
      gridColor2: 0x3b0764
    }
  }
};
