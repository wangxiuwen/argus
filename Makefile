APP     = Argus
VERSION = 0.1.0
OUT     = .build/$(APP)
BUNDLE  = $(HOME)/Applications/$(APP).app
BIN_DIR = $(HOME)/.local/bin
STAGE   = .build/stage
DIST    = dist/argus-$(VERSION)-macos-arm64.tar.gz

.PHONY: build install uninstall clean dist

build:
	mkdir -p .build
	swiftc -O -o $(OUT) Argus/main.swift

install: build
	mkdir -p $(BUNDLE)/Contents/MacOS $(BIN_DIR)
	cp $(OUT) $(BUNDLE)/Contents/MacOS/
	cp Info.plist $(BUNDLE)/Contents/
	codesign --force --sign - $(BUNDLE)
	install -m 755 bin/argus $(BIN_DIR)/argus
	mkdir -p $(HOME)/.local/share/argus
	install -m 644 share/chat.py share/ui.py share/ui.html share/settings.html \
		share/launch.py share/bridge.py share/prune.py $(HOME)/.local/share/argus/
	@echo "installed: $(BUNDLE) and $(BIN_DIR)/argus"
	@echo "open the tray app with: open $(BUNDLE)"

uninstall:
	-$(BIN_DIR)/argus stop
	rm -rf $(BUNDLE)
	rm -f $(BIN_DIR)/argus
	rm -rf $(HOME)/.local/share/argus

dist: build
	rm -rf $(STAGE) && mkdir -p $(STAGE)/$(APP).app/Contents/MacOS $(STAGE)/bin $(STAGE)/share dist
	cp $(OUT) $(STAGE)/$(APP).app/Contents/MacOS/
	cp Info.plist $(STAGE)/$(APP).app/Contents/
	codesign --force --sign - $(STAGE)/$(APP).app
	cp bin/argus $(STAGE)/bin/
	cp share/* $(STAGE)/share/
	cp install.sh README.md LICENSE $(STAGE)/
	chmod +x $(STAGE)/install.sh $(STAGE)/bin/argus
	tar -czf $(DIST) -C $(STAGE) .
	@echo "built $(DIST)"

clean:
	rm -rf .build dist
