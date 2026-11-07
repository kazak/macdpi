import AppKit
let size = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                          bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                          isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSColor.clear.setFill()
NSRect(x: 0, y: 0, width: size, height: size).fill()
let shape = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 208, yRadius: 208)
NSGradient(starting: NSColor(srgbRed: 0.99, green: 0.28, blue: 0.32, alpha: 1),
           ending: NSColor(srgbRed: 0.72, green: 0.06, blue: 0.17, alpha: 1))!.draw(in: shape, angle: -75)
NSGraphicsContext.saveGraphicsState()
shape.addClip()
NSColor.white.withAlphaComponent(0.08).setFill()
NSBezierPath(ovalIn: NSRect(x: -160, y: 490, width: 1400, height: 800)).fill()
NSGraphicsContext.restoreGraphicsState()
let play = NSBezierPath()
play.move(to: NSPoint(x: 415, y: 320))
play.line(to: NSPoint(x: 415, y: 704))
play.line(to: NSPoint(x: 733, y: 512))
play.close()
let shadow = NSShadow()
shadow.shadowOffset = NSSize(width: 0, height: -12)
shadow.shadowBlurRadius = 25
shadow.shadowColor = NSColor.black.withAlphaComponent(0.15)
shadow.set()
NSColor.white.setFill()
play.fill()
NSShadow().set()
play.lineJoinStyle = .round
play.lineWidth = 24
NSColor.white.setStroke()
play.stroke()
NSGraphicsContext.restoreGraphicsState()
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
