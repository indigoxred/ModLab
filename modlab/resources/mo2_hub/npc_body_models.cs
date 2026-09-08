public static void ApplyModLabModels(Npc npc, IArmorGetter skin, string actor, PatcherSettings settings, ILinkCache<ISkyrimMod, ISkyrimModGetter> cache, ISkyrimMod patch)
        {
            if (!settings.ModLabBodyModelSources.TryGetValue(actor, out var models))
            {
                npc.WornArmor.SetTo(skin.FormKey);
                return;
            }
            if (!settings.ModLabBodySex.TryGetValue(actor, out var sex) || (sex != "female" && sex != "male"))
                throw new Exception("Character body sex is missing or unsupported");
            var previousNext = patch.NextFormID;
            patch.NextFormID = ModLabStableId(patch, actor + "|body");
            var copy = patch.Armors.DuplicateInAsNewRecord(skin);
            patch.NextFormID = Math.Max(previousNext, patch.NextFormID);
            copy.EditorID = "ModLabBody_" + npc.FormKey.ID.ToString("X6");
            copy.Armature.Clear();
            foreach (var link in skin.Armature)
            {
                if (!models.TryGetValue(link.FormKey.ToString().ToLowerInvariant(), out var modelKey))
                {
                    copy.Armature.Add(link);
                    continue;
                }
                var original = cache.Resolve<IArmorAddonGetter>(link.FormKey);
                var donor = cache.Resolve<IArmorAddonGetter>(FormKey.Factory(modelKey));
                previousNext = patch.NextFormID;
                patch.NextFormID = ModLabStableId(patch, actor + "|part|" + link.FormKey.ToString());
                var addon = patch.ArmorAddons.DuplicateInAsNewRecord(original);
                patch.NextFormID = Math.Max(previousNext, patch.NextFormID);
                addon.WorldModel ??= new GenderedItem<Model>(null, null);
                addon.FirstPersonModel ??= new GenderedItem<Model>(null, null);
                if (addon.WorldModel == null) throw new Exception("Shared body has no world model");
                if (sex == "female")
                {
                    addon.WorldModel.Female = donor.WorldModel?.Female?.DeepCopy();
                    if (addon.FirstPersonModel != null) addon.FirstPersonModel.Female = donor.FirstPersonModel?.Female?.DeepCopy();
                }
                else
                {
                    addon.WorldModel.Male = donor.WorldModel?.Male?.DeepCopy();
                    if (addon.FirstPersonModel != null) addon.FirstPersonModel.Male = donor.FirstPersonModel?.Male?.DeepCopy();
                }
                if (settings.ModLabSkinTextures.TryGetValue(actor, out var skins) && skins.TryGetValue(link.FormKey.ToString().ToLowerInvariant(), out var textures))
                {
                    var originalTexture = sex == "female" ? original.SkinTexture?.Female : original.SkinTexture?.Male;
                    ITextureSetGetter textureSource = null;
                    if (originalTexture != null && !originalTexture.IsNull)
                        textureSource = cache.Resolve<ITextureSetGetter>(originalTexture.FormKey);
                    var texture = ModLabSkinTexture(patch, actor + "|skin|" + link.FormKey.ToString(), textureSource, textures);
                    addon.SkinTexture ??= new GenderedItem<IFormLinkNullableGetter<ITextureSetGetter>>(new FormLinkNullable<ITextureSetGetter>(), new FormLinkNullable<ITextureSetGetter>());
                    if (sex == "female") addon.SkinTexture.Female = new FormLinkNullable<ITextureSetGetter>(texture.FormKey);
                    else addon.SkinTexture.Male = new FormLinkNullable<ITextureSetGetter>(texture.FormKey);
                    var swapLink = sex == "female" ? original.TextureSwapList?.Female : original.TextureSwapList?.Male;
                    if (swapLink != null && !swapLink.IsNull)
                    {
                        var sourceList = cache.Resolve<IFormListGetter>(swapLink.FormKey);
                        if (sourceList.Items.Count != 1 || !cache.TryResolve<ITextureSetGetter>(sourceList.Items[0].FormKey, out var unused))
                            throw new Exception("Skin swap variants need a matching conversion before replacing this skin");
                        previousNext = patch.NextFormID;
                        patch.NextFormID = ModLabStableId(patch, actor + "|skin-swap|" + link.FormKey.ToString());
                        var swapList = patch.FormLists.DuplicateInAsNewRecord(sourceList);
                        patch.NextFormID = Math.Max(previousNext, patch.NextFormID);
                        swapList.Items.Clear();
                        swapList.Items.Add(new FormLink<ISkyrimMajorRecordGetter>(texture.FormKey));
                        if (sex == "female") addon.TextureSwapList.Female = new FormLinkNullable<IFormListGetter>(swapList.FormKey);
                        else addon.TextureSwapList.Male = new FormLinkNullable<IFormListGetter>(swapList.FormKey);
                    }
                }
                copy.Armature.Add(addon.AsLinkGetter());
            }
            npc.WornArmor.SetTo(copy.FormKey);
            if (settings.ModLabHeadSkinTextures.TryGetValue(actor, out var headTextures))
            {
                ITextureSetGetter headSource = null;
                if (!npc.HeadTexture.IsNull) headSource = cache.Resolve<ITextureSetGetter>(npc.HeadTexture.FormKey);
                npc.HeadTexture.SetTo(ModLabSkinTexture(patch, actor + "|head-skin", headSource, headTextures).FormKey);
            }
        }

        public static TextureSet ModLabSkinTexture(ISkyrimMod patch, string identity, ITextureSetGetter source, Dictionary<string,string> textures)
        {
            var previousNext=patch.NextFormID;patch.NextFormID=ModLabStableId(patch,identity);
            var result=source == null ? patch.TextureSets.AddNew() : patch.TextureSets.DuplicateInAsNewRecord(source);
            patch.NextFormID=Math.Max(previousNext,patch.NextFormID);
            foreach(var role in new[]{"diffuse","normal","subsurface","specular"})
                if(!textures.TryGetValue(role,out var path) || String.IsNullOrWhiteSpace(path))throw new Exception("Incomplete skin texture set: "+role);
            result.Diffuse=textures["diffuse"];result.NormalOrGloss=textures["normal"];
            result.EnvironmentMaskOrSubsurfaceTint=textures["subsurface"];result.BacklightMaskOrSpecular=textures["specular"];
            return result;
        }

        public static uint ModLabStableId(ISkyrimMod patch, string identity)
        {
            using var hash = System.Security.Cryptography.SHA256.Create();
            var bytes = hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(identity.ToLowerInvariant()));
            uint value = 0x800 + (((uint)bytes[0] << 16 | (uint)bytes[1] << 8 | bytes[2]) % 0xFFF800);
            if (patch.EnumerateMajorRecords().Any(r => r.FormKey.ModKey == patch.ModKey && r.FormKey.ID == value))
                throw new Exception("Generated character record ID collision. Output was withheld; existing choices remain installed.");
            return value;
        }
