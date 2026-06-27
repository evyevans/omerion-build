import re

with open('/Users/evy/Desktop/OMERION AI EMPLOYEES/dashboard/src/components/PixelOffice.tsx', 'r') as f:
    content = f.read()

# Add imports
imports = """import { getCharacterSprites, CHARACTER_PALETTES } from './pixel-engine/sprites/spriteData';
import { getCachedSprite } from './pixel-engine/sprites/spriteCache';
import { Direction } from './pixel-engine/types';
import type { CharacterSprites } from './pixel-engine/sprites/spriteData';
import type { SpriteData } from './pixel-engine/types';"""
content = re.sub(r"(import type \{ AgentDef, AgentState, AgentStatus \} from '../types';)", r"\1\n\n" + imports, content)

# Remove old BASE_SPRITE_PATHS logic
content = re.sub(r"// ── Asset Sources \(6 base sprite sheets\) ──────────────────────────────────\nconst BASE_SPRITE_PATHS = \[\n.*?\];\n", "", content, flags=re.DOTALL)

# Update uniqueSprites type
content = re.sub(r"type UniqueSprites = HTMLCanvasElement\[\];", "type UniqueSprites = CharacterSprites[];", content)

# Update CHAR_W and CHAR_H
content = re.sub(r"const CHAR_W = 16;\nconst CHAR_H = 32;", "const CHAR_W = 16;\nconst CHAR_H = 24;", content)

# Update useEffect for loading
old_load_effect = """  // Load 6 base sprites, then pre-render 14 unique tinted variants
  useEffect\(\(\) => \{
    let loadedCount = 0;
.*?
    function buildUniqueSprites\(bases: HTMLImageElement\[\]\) \{.*?
      setUniqueSprites\(sprites\);
    \}
  \}, \[\]\);"""

new_load_effect = """  // Load procedural sprites directly via pixel engine
  useEffect(() => {
    const sprites = AGENT_SPRITE_CONFIG.map(([baseIdx, hue]) => {
      const palette = CHARACTER_PALETTES[baseIdx % CHARACTER_PALETTES.length];
      return getCharacterSprites(palette, hue);
    });
    setUniqueSprites(sprites);
  }, []);"""

content = re.sub(old_load_effect, new_load_effect, content, flags=re.DOTALL)

# Update rendering loop
old_render_logic = """          // Get this agent's unique pre-rendered sprite canvas
          const agentIdx = parseInt\(ch\.spriteName\.replace\('agent', ''\), 10\);
          const sprite = uniqueSprites\[agentIdx\];
          
          // Row mapping: 0=Down, 1=Up, 2=Right, 3=Left \(Uses Right row but flipped\)
          const sRow = ch\.walkDir === 3 \? 2 : ch\.walkDir; 
          const isFlipped = ch\.walkDir === 3;
          
          // Column mapping: Sprite columns are \[0:Walk1, 1:Idle, 2:Walk2\]
          const walkCycle = \[0, 1, 2, 1\];
          const sCol = ch\.isWalking \? walkCycle\[Math\.floor\(ch\.walkFrame\)\] : 1;
          
          if \(sprite\) \{
             ctx\.save\(\);
             // Center translation for flipping
             ctx\.translate\(Math\.floor\(ch\.x\) \+ CHAR_W, Math\.floor\(ch\.y\)\);
             if \(isFlipped\) \{
                ctx\.scale\(-1, 1\);
             \}
             ctx\.drawImage\(
               sprite,
               sCol \* CHAR_W, sRow \* CHAR_H, CHAR_W, CHAR_H,
               -CHAR_W, 0, CHAR_W \* 2, CHAR_H \* 2
             \);
             ctx\.restore\(\);
          \}"""

new_render_logic = """          // Get this agent's procedural CharacterSprites
          const agentIdx = parseInt(ch.spriteName.replace('agent', ''), 10);
          const sprites = uniqueSprites[agentIdx];
          
          let engineDir: Direction;
          if (ch.walkDir === 0) engineDir = Direction.DOWN;
          else if (ch.walkDir === 1) engineDir = Direction.UP;
          else if (ch.walkDir === 2) engineDir = Direction.RIGHT;
          else engineDir = Direction.LEFT;

          let spriteData: SpriteData | null = null;
          if (ch.isWalking) {
             const walkCycle = [0, 1, 2, 1];
             const frame = walkCycle[Math.floor(ch.walkFrame) % 4];
             spriteData = sprites.walk[engineDir][frame];
          } else if (status === 'active' || status === 'running') {
             const timeFrame = Math.floor(performance.now() / 250) % 2;
             spriteData = sprites.typing[engineDir][timeFrame];
          } else {
             spriteData = sprites.walk[engineDir][1];
          }

          if (spriteData) {
             const cached = getCachedSprite(spriteData, 2.0); // 2x scale crisp rendering
             const renderX = Math.round(ch.x);
             const renderY = Math.round(ch.y);
             ctx.drawImage(cached, renderX, renderY);
          }"""

content = re.sub(old_render_logic, new_render_logic, content, flags=re.DOTALL)

with open('/Users/evy/Desktop/OMERION AI EMPLOYEES/dashboard/src/components/PixelOffice.tsx', 'w') as f:
    f.write(content)

